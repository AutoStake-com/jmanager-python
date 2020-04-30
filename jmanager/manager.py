import threading
from datetime import datetime, timedelta
from logging import getLogger
import json
import traceback
import time
import os
from jormungandr import Jormungandr
from error_types import *
from jm_enums import State
from pool_tool import PoolTool
from jm_email import Email
import utils

log = getLogger(utils.get_module_name(os.path.basename(__file__)))

class Manager(threading.Thread):
    _LOOP_INTERVAL = 1      # how fast main loop turns (in seconds)

    def __init__(self, config):
        threading.Thread.__init__(self, name='manager')
        self._config = config
        self._config_last_updated = None
        self._update_config_if_new()

        self._max_node_reported_tip = 0
        self._leader_nodes = []
        self._slots_assigned = []
        self.node_threads = []
        self._pool_tool = PoolTool(self._config)

        config_manager_settings = self._config.get_config_manager()
        for node_config in config_manager_settings['nodes']:
            node_thread = Jormungandr(self._config, node_config['node_name'], self.node_threads)
            self.node_threads.append(node_thread)
            node_thread.start()

        log.info('Created {} threads.'.format(len(self.node_threads)))

    def _update_config_if_new(self):
        if self._config.is_config_update_needed(self._config_last_updated):

            config_manager_settings = self._config.get_config_manager()
            self._timeout_between_restarts = config_manager_settings['manager']['timeout_between_restarts']
            self._min_scheduled_time_difference = config_manager_settings['manager']['min_scheduled_time_difference']
            self._send_slots_within_time = config_manager_settings['manager']['send_slots_within']
            self._slots_sent_epoch = 0
            
            config_email = self._config.get_config_email()
            if (config_email['email_alerts'] == 1):
                self._email = Email(self._config)
            else:
                self._email = None

            epoch_time = config_manager_settings['manager']['epoch_start_time']
            self._epoch_start_time = {'hour': epoch_time['hour'], 'minute': epoch_time['minute'], 'second': epoch_time['second'] } # UTC time

            self._config_last_updated = self._config.get_latest_config_timestamp()
            self._pool_id = self._read_file(config_manager_settings['manager']['pool_id_file']).strip()
            self._genesis_hash = self._read_file(config_manager_settings['manager']['genesis_hash_file']).strip()

    def _read_file(self, filename):
        content = None
        try:
            with open(filename, 'r') as data_file:
                content = data_file.read()
        except Exception as e:
            log.error('Exception occured', exc_info=True)

        return content

    def _get_timeout_between_restarts(self, unit='sec'):
        if unit == 'sec':
            return self._timeout_between_restarts
        elif unit == 'min':
            return self._timeout_between_restarts / 60

    def _update_max_tip(self, node):
        if node.get_state() == State.STARTED:
            new_tip = node.get_tip()
            if new_tip != None and new_tip > self._max_node_reported_tip:
                self._max_node_reported_tip = new_tip
                self._pool_tool.refresh_data_for_tip_update(node.get_last_stats(), node.get_last_block(), self._pool_id, self._genesis_hash)

    # gets the max tip of the tips reported by running nodes
    def _get_nodes_max_tip(self):
        return self._max_node_reported_tip

    # get absolute max tip of the tips reported by running nodes and pooltool.io
    def _get_max_tip(self):
        return max(self._get_nodes_max_tip(), self._pool_tool.get_max_tip())

    def _check_leaders(self):
        self._leader_nodes = []
        node_with_max_tip = None

        for node in self.node_threads:
            if node.get_state() != State.STARTED:
                continue

            # check if the current node with the max tip is still the node with the max tip - difference must be at least 2
            # since the stats can be old a few seconds and not syncrhonized and we don't want
            # to switch between nodes too often for nothing
            if node_with_max_tip == None or (node.get_tip() - 3) >= self._get_nodes_max_tip():
                if node_with_max_tip != None:
                    log.debug('Change to node with max tip: {}:{} ==>  {}:{}'.format(node_with_max_tip.get_name(), self._get_nodes_max_tip(), node.get_name(), node.get_tip()))
                node_with_max_tip = node

            # if node is a leader add it to the leaders list
            leaders = node.get_leaders()
            if node.is_leader() and leaders != None and len(leaders) > 0:
                self._leader_nodes.append({'id': leaders[0], 'node': node})

        leaders_count = len(self._leader_nodes)
        if leaders_count == 1:
            if node_with_max_tip.get_name() != self._leader_nodes[0]['node'].get_name():
                log.info("Switching from leader node {} to better synced node {}.".format(self._leader_nodes[0]['node'].get_name(), node_with_max_tip.get_name()))
                node_with_max_tip.register_leader()
                log.info("Registered leader {}.".format(node_with_max_tip.get_name()))
                self._leader_nodes[0]['node'].unregister_leader(self._leader_nodes[0]['id'])
                log.info("Unregistered leader {}.".format(self._leader_nodes[0]['node'].get_name()))                
        elif leaders_count > 1:
            log.warning("Got multiple ({}) leaders!".format(leaders_count))
            for leader in self._leader_nodes:
                if leader['node'].get_name() != node_with_max_tip.get_name():
                    leader['node'].unregister_leader(leader['id'])
                    log.info("Unregistered leader {}.".format(leader['node'].get_name()))
        elif leaders_count == 0 and (node_with_max_tip != None):
            log.warning("No leader nodes found. Registering node '{}' as leader.".format(node_with_max_tip.get_name()))
            is_registered = node_with_max_tip.register_leader()
            if is_registered == "1":
                log.debug("Registered node {}".format(node_with_max_tip.get_name()))

    def _get_epoch_start_datetime(self):
        dt = datetime.utcnow()
        return datetime(dt.year, dt.month, dt.day, self._epoch_start_time['hour'], self._epoch_start_time['minute'], self._epoch_start_time['second'])

    def _restart_nodes_for_slot_assignments(self):
        if len(self._leader_nodes) == 0:
            return

        current_epoch = self._leader_nodes[0]['node'].get_current_epoch()

        for item in self._slots_assigned:
            if item['epoch'] == current_epoch:
                for node in self.node_threads:
                    if node.get_name() not in item['nodes']:
                        slots_assigned = node.get_leaders_logs()
                        if slots_assigned is None:
                            continue

                        # we cannot compare the whole lists because they can have different creation dates
                        # so we check just the scheduled slots
                        valid_slots = []
                        for s in item['slots']:
                            valid_slots.append(s['scheduled_at_date'])
                        valid_slots.sort()

                        node_slots = []
                        for s in slots_assigned:
                            node_slots.append(s['scheduled_at_date'])
                        node_slots.sort()

                        if json.dumps(valid_slots) == json.dumps(node_slots):
                            item['nodes'].append(node.get_name())
                        elif len(slots_assigned) == 0:
                            if len(item['slots']) > 0:
                                # do get the closest scheduled slot time and if we are far enough from it (self._min_scheduled_time_difference)
                                # and if there are any other nodes up, restart the node
                                dt = datetime.utcnow()
                                closest_scheduled_slot = None
                                for slot in item['slots']:
                                    # python3.8 can automatically convert time but python3.6 needs to get rid of ':'
                                    ts_dt = datetime.strptime(slot['scheduled_at_time'][:19],'%Y-%m-%dT%H:%M:%S')
                                    if ts_dt < dt:
                                        continue

                                    if closest_scheduled_slot is None:
                                        closest_scheduled_slot = ts_dt

                                    closest_scheduled_slot = ts_dt if ts_dt < closest_scheduled_slot else closest_scheduled_slot

                                if self._is_any_other_node_up(node) and (closest_scheduled_slot - datetime.utcnow()).seconds > self._min_scheduled_time_difference and node.get_state() == State.STARTED:
                                    log.debug("Restarting node so it can get its assigned slots schedule.")
                                    node.restart(reason='leader logs')
                            else:
                                log.warning('Node {} does not report any slots assigned while other nodes do: {}'.format(node.get_name(), item['nodes']))
                        else:
                            log.error('Nodes report different slots!')

    def _send_slots(self):
        # send slots too pool tool (only send slots if between _send_slots_within_time in epoch and _send_slots_within_time + 60 )
        if len(self._leader_nodes) == 0:
            return

        current_epoch = self._leader_nodes[0]['node'].get_current_epoch()
        if not self._slots_sent_epoch == current_epoch:
            return

        epoch_start_time = self._get_epoch_start_datetime()
        dt = datetime.utcnow()
        log.debug('Check if slots OK')
        if dt > epoch_start_time:
            log.debug('Send slots2.')
            if (dt - epoch_start_time).seconds > self._send_slots_within_time and (dt - epoch_start_time).seconds < (self._send_slots_within_time + 60):
                self._pool_tool.send_slots(self._leader_nodes[0]['node'].get_api_endpoint(), self._pool_id, self._genesis_hash)
                self._slots_sent_epoch = current_epoch
                log.debug('Slots sent!')

    def _check_slot_assignments(self):
        if len(self._leader_nodes) == 0:
            log.warning("Cannot get leader logs. No leader nodes found.")
            return

        current_epoch = self._leader_nodes[0]['node'].get_current_epoch()
        for item in self._slots_assigned:
            if item['epoch'] == current_epoch:
                return

        slots_assigned = self._leader_nodes[0]['node'].get_leaders_logs()
        self._slots_assigned.append({'epoch': current_epoch, 'nodes': [self._leader_nodes[0]['node'].get_name()], 'slots': slots_assigned})
        log.debug(json.dumps(slots_assigned, indent=4))
        self._send_email('slots_assigned', {'timestamp': datetime.now(), 'node_name': '', 'slots': slots_assigned})

        # remove any slots from previous epoch
        if len(self._slots_assigned) > 1:
            del self._slots_assigned[0]

    def _send_email(self, email_template, template_parameters):
        if self._email == None:
            return

        self._email.send(email_template, template_parameters)

    def run(self):
        dt = datetime.now()
        while True:
            try:
                time.sleep(Manager._LOOP_INTERVAL)
                if (datetime.now() - dt).seconds < Manager._LOOP_INTERVAL:
                    continue

                self._pool_tool._update_config_if_new()
                self._pool_tool._get_status_summary()
                self._pool_tool.send_my_tip()

                self._update_config_if_new()

                dt = datetime.now()
                
                # verify number of leaders and make sure only 1 leader is active
                self._check_leaders()

                # get any new assigned slots
                self._check_slot_assignments()
                
                # sends slots to pooltool if not done alreay
                self._send_slots()
                
                # restart nodes at the beginning of epoch so each of them can get its own slot assignment schedule
                self._restart_nodes_for_slot_assignments()

                # check each node's state and act accordingly
                for node in self.node_threads:
                    self._update_max_tip(node)
                    # if this is first main loop run and there are no running_nodes, peers need to be adjusted
                    # as they won't be able to bootstrap from each other
                    if node.get_state() == State.STARTED:
                        if node.is_stuck(self._get_max_tip()):
                            # need to check if the other node is running...cannot have all nodes rebooting at same time
                            log.info("Tip has not been updated for {} minutes. Restarting node.".format(datetime.now(), node.get_tip_timeout('min')))
                            node.restart(reason='staled tip')
                            self._send_email('stuck', {'timeout': node.get_tip_timeout('min'), 'node_name': node.get_name()})
                        continue
                    elif node.get_state() == State.BOOTSTRAPPING:
                        # if bootstrapping for too long, restart
                        if node.get_seconds_since_bootstrap_started() > self._get_timeout_between_restarts('sec'):
                            if self._is_any_other_node_up(node):
                                log.info("Bootstrapping for more than {} min. Restarting node {}.".format(self._get_timeout_between_restarts('min'), node.get_name()))
                                node.restart(reason='boot timeout')
                            else:
                                log.info("Bootstrapping for more than {} min. Restarting node {} with default peers config.".format(self._get_timeout_between_restarts('min'), node.get_name()))
                                node.switch_to_default_peers_bootstrap()
                                node.restart(reason='boot timeout')
                                node.switch_to_fast_bootstrap()

                            self._send_email('bootstrap_restart', {'timeout': self._get_timeout_between_restarts('min'), 'node_name': node.get_name()})
                        continue
                    # restart app if it is not beeing restarted already
                    elif node.get_state() == State.STOPPED:
                        log.debug("{}: Stopped".format(node.get_name()))
                        # only restart node if at least one other node is running (fast rebooting)
                        if self._is_any_other_node_up(node):
                            log.info("Node {} is not running".format(node.get_name()))
                            node.start_node()
                        else:
                            self._start_all_nodes()
                        continue
                    
                    if not self._is_any_node_up():
                        log.warning("No nodes running. Starting all nodes.")
                        self._start_all_nodes()
                        continue

                    log.warning("Node {} state is {}!".format(node.get_name(), node.get_state()))
            except JcliError as e:
                e.print_error()
            except Exception as e:
                log.error('Exception occured', exc_info=True)

    def _is_any_node_up(self):
        for node in self.node_threads:
            if node._state == State.STARTED:
                return True
        return False

    def _is_any_other_node_up(self, exclude_node):
        node_active = False
        for n in self.node_threads:
            if n.get_name() == exclude_node.get_name():
                continue
            if n.get_state() == State.STARTED:
                node_active = True
                break

        if node_active:
            return True
        else:
            return False

    # if none of the nodes is up then start all nodes
    def _start_all_nodes(self):
        if not self._is_any_node_up():
            if len(self.node_threads) > 0:
                for node in self.node_threads:
                    node.switch_to_default_peers_bootstrap()
                    if (node.get_state() == State.STOPPED):
                        node.start_node()
                    else:
                        log.info("Cannot start node. Node '{}' is not stopped ({}).".format(node.get_name(), node.get_state()))
            else:
                log.info("There are no node threads to start.")
        else:
            log.info("Nodes are already started.")

