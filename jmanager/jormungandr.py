from subprocess import Popen, PIPE
from datetime import datetime, timedelta
import time 
import json
import requests
from requests.exceptions import HTTPError
import threading
import sys
import os
from jm_enums import State, JError
from logging import getLogger
from error_types import *
from xmlrpc.client import ServerProxy
import utils
import threading

threadLock = threading.Lock()

log = getLogger(utils.get_module_name(os.path.basename(__file__)))

class Jormungandr(threading.Thread):
    def __init__(self, config, node_name, jormungandr_nodes):
        threading.Thread.__init__(self, name=node_name)
        self._config = config

        # node threads
        self._jormungandr_nodes = jormungandr_nodes

        self._config_last_updated = None
        self._update_config_if_new()

        # bootstrap timestamp for current node instance
        self._bootstrap_started_at_time = None

        log.debug("Created node thread {}".format(self._node_name))

    def _update_config_if_new(self):
        if self._config.is_config_update_needed(self._config_last_updated):
            config_data = self._config.get_config(self.name)
            if config_data == None:
                raise Exception("Could not obtain configuration for node instance '{}'".format(self.name)) 

            # node configuration related variables
            cmn_cfg = config_data['common_config_jormungandr']

            self._refresh_interval = cmn_cfg['timeouts']['refresh_interval']
            self._tip_diff_threshold = cmn_cfg['tip_diff_threshold']
            self._tip_timeout = cmn_cfg['timeouts']['tip_timeout']
            self._check_leaders_refresh_interval = cmn_cfg['timeouts']['leaders_refresh_interval']
            self._jormungandr_common_dir = cmn_cfg['common_dir']
            self._restarts_log_filename = cmn_cfg['restarts_log_filename']
            self._node_name = config_data['node_name']
            self._config_filename = config_data['filename']
            self._host = "http://{}/api".format(config_data['config']['rest']['listen'])
            self._jormungandr_dir = config_data['jmanager_settings']['node_path']
            self._jcli = "{}/jcli".format(self._jormungandr_dir)
            self._supervisor_service_name = config_data['jmanager_settings']['supervisor_service_name']
            self._default_peers = config_data['jmanager_settings']['default_trusted_peers']
            self._restarts_logs = "{}/{}".format(self._jormungandr_common_dir, self._restarts_log_filename)
            self._leader_secret_file = "{}/{}".format(self._jormungandr_common_dir, cmn_cfg['secret'])
            self._server = ServerProxy(cmn_cfg['supervisor_rest_api_url'])

            # variables holding state info of this node instance
            self._node_stats = None
            self._previous_node_stats = None
            self._node_stats_time = None
            self._jmconfig = config_data['config']
            self._jmconfig_copy = None
            self._default_peers_enabled = False
            self._leaders = None
            self._last_time_check_leaders = None

            # save configuration to file
            self._save_config()

            # set initial state node
            self._state = State.UNKNOWN
            self._config_last_updated = self._config.get_latest_config_timestamp()

    def _load_config(self):
        with open(self._config_filename, 'r') as json_file:
            self._jmconfig = json.loads(json_file)
        log.debug("Config loaded from {}".format(self._config_filename))

    def _save_config(self):
        with open(self._config_filename, 'w') as json_file:
            json_file.write(json.dumps(self._jmconfig, indent=4))
            log.debug("Config: {}".format(json.dumps(self._jmconfig, indent=2)))
        log.debug("Config saved to {}".format(self._config_filename))

    def _log_action(self, action='', reason=''):
        header = ''
        if not os.path.exists(self._restarts_logs):
            header = 'node name, timestamp, action, uptime, reason\n'

        with open(self._restarts_logs, 'a') as f:
            f.write('{}{},{},{},{},{}\n'.format(header, self.get_name(), datetime.utcnow(), action, self.get_uptime(), reason))

    def _set_state(self, state):
        self._state = state

    def _clean_up(self):
        self._node_stats_time = None
        self._node_stats = None
        self._previous_node_stats = None
        self._leaders = None

    def _get_stats(self):
        threadLock.acquire()
        command = [self._jcli, "rest", "v0", "node", "stats", "get", "-h", self._host, "--output-format", "json"]
        proc = Popen(command, stdout=PIPE, stderr=PIPE)
        stdout, stderr = proc.communicate()
        if proc.returncode != 0:
            err_msg = stderr.decode()
            # jormungandr returns 1 on error, so we parse the output to get the error type
            msg_node_down = "failed to make a REST request"
            msg_address_already_in_use = "Address already in use"

            err_code = JError.UNKNOWN
            if err_msg.find(msg_node_down) > -1:
                err_code = JError.FAILED_REST_REQUEST
            elif err_msg.find(msg_address_already_in_use) > 0:
                err_code = JError.ADDRESS_ALREADY_IN_USE

            self.set_state_from_supervisor()

            threadLock.release()
            raise JcliError('Could not get node stats.', err = {'proc_ret_code': proc.returncode, 'err_code': err_code, 'stdout': stdout.decode(), 'stderr': stderr.decode()})

        if proc.returncode == 0:
            node_stats = json.loads(stdout.decode())
            state = node_stats.get('state')
            if state == 'Bootstrapping':
                self._set_state(State.BOOTSTRAPPING)
                threadLock.release()
                return None
            elif node_stats.get('lastBlockHeight'):
                self._set_state(State.STARTED)
            else:
                self.set_state_from_supervisor()

                self._clean_up()
                threadLock.release()
                return None

            if self._previous_node_stats is None:
                self._node_stats_time = datetime.now()
                self._previous_node_stats = node_stats
                self._node_stats = node_stats
            else:
                if node_stats['lastBlockHeight'] > self._previous_node_stats['lastBlockHeight']:
                    self._previous_node_stats = self._node_stats
                    self._node_stats = node_stats
                    self._node_stats_time = datetime.now()

        threadLock.release()
        return self._node_stats

    # executes jcli and gets leaders - tells if the node runs as a leader or not
    def _get_leaders(self):
        if threadLock.locked():
            return None

        threadLock.acquire()

        if self._state != State.STARTED:
            threadLock.release()
            return None

        log.debug("{} state: {}".format(self.get_name(), self._state))

        command = [self._jcli, "rest", "v0", "leaders", "get", "-h", self._host, "--output-format", "json"]
        proc = Popen(command, stdout=PIPE, stderr=PIPE)

        stdout, stderr = proc.communicate()
        if proc.returncode != 0:
            threadLock.release()
            raise JcliError('An error occurred while getting leaders', err = {'proc_ret_code': proc.returncode, 'err_code': 1, 'stdout': stdout.decode(), 'stderr': stderr.decode()})

        self._leaders = json.loads(stdout.decode())

        self._last_time_check_leaders = datetime.now()

        threadLock.release()
        return self._leaders

    def get_last_block(self):
        if self._state == State.STARTED:
            stats = self.get_last_stats()
            if stats == None:
                return None

            command = [self._jcli, "rest", "v0", "block", stats['lastBlockHash'], "get", "-h", self._host]
            proc = Popen(command, stdout=PIPE, stderr=PIPE)
            stdout, stderr = proc.communicate()

            if proc.returncode != 0:
                raise JcliError('An error occurred while getting block from blockhash', err = {'proc_ret_code': proc.returncode, 'err_code': 1, 'stdout': stdout.decode(), 'stderr': stderr.decode()})

            return stdout.decode()
        else:
            log.error('Cannot get block. {} is not running.'.format(self.get_name()))

    def get_supervisor_service_uptime(self):
        proc_info = self._server.supervisor.getProcessInfo(self._supervisor_service_name)
        uptime = proc_info['now'] - proc_info['start']

        return uptime

    def get_supervisor_service_state(self):
        proc_info = self._server.supervisor.getProcessInfo(self._supervisor_service_name)

        return proc_info['state']

    def is_supervisor_node_up(self):
        pcode = self.get_supervisor_service_state()

        if pcode == 20 or pcode == 10:
            return True
        else:
            return False

    def set_state_from_supervisor(self):
        proc_info = self._server.supervisor.getProcessInfo(self._supervisor_service_name)
        pcode = proc_info['state']
        if pcode == 0 or pcode == 40:
            self._set_state(State.STOPPED)
        elif pcode == 20:
            self._set_state(State.STARTED)
        elif pcode == 10:
            self._set_state(State.BOOTSTRAPPING)
        else:
            self._set_state(State.UNKNOWN)

    def get_api_endpoint(self):
        return self._host + "/v0"

    def get_current_epoch(self):
        return self._node_stats['lastBlockDate'].split('.')[0]

    def get_uptime(self):
        return int(self._node_stats['uptime']) if self._node_stats != None and self._node_stats['uptime'] != None else -1

    def get_last_stats(self):
        return self._node_stats

    def get_name(self):
        return self._node_name

    def switch_to_default_peers_bootstrap(self):
        self._jmconfig_copy = self._jmconfig
        if self._jmconfig != None:
            log.debug("Switching to default peers config: {}".format(json.dumps(self._jmconfig['p2p'])))
            self._jmconfig['p2p']['trusted_peers'] = self._default_peers
            self._save_config()
            self._default_peers_enabled = True

    def switch_to_fast_bootstrap(self):
        if self._jmconfig_copy != None:
            log.debug("Switching to fast boot peers config: {}".format(self._jmconfig_copy))
            self._jmconfig = self._jmconfig_copy
            self._save_config()
            self._default_peers_enabled = False

    def get_state(self):
        return self._state

    def get_tip(self):
        if self._node_stats != None:
            blockHeight = self._node_stats.get('lastBlockHeight')
            if blockHeight != None:
                return int(self._node_stats['lastBlockHeight'])
        return 0

    def get_tip_timeout(self, unit='sec'):
        if unit == 'sec':
            return self._tip_timeout
        elif unit == 'min':
            return self._tip_timeout / 60

    def is_stuck(self, max_tip):
        if self._previous_node_stats is None:
            return False    # we don't have the info yet

        if self._previous_node_stats['lastBlockHeight'] == int(self._node_stats['lastBlockHeight']) and (datetime.now() - self._node_stats_time).seconds > self._tip_timeout:
            log.warn("Node's tip has been the same ({}) for {} seconds.".format(self._node_stats['lastBlockHeight'], self._tip_timeout))
            return True

        if abs(int(self._node_stats['lastBlockHeight']) - max_tip) > self._tip_diff_threshold:
            log.warn("Node is off by more than {} from max tip {}".format(self._tip_diff_threshold, max_tip))
            return True

        return False

    def get_seconds_since_bootstrap_started(self):
        if self._bootstrap_started_at_time is None:
            self._bootstrap_started_at_time = datetime.now()

        return (datetime.now() - self._bootstrap_started_at_time).seconds

    def stop_node(self, force = True, reason=''):
        if self.is_supervisor_node_up() and (self._state == State.STARTED or self._state == State.BOOTSTRAPPING or force):
            self._log_action('stop', reason)

            success = self._server.supervisor.stopProcess(self._supervisor_service_name)
            if not success:
                raise SupervisorError("Failed to stop {}".format(self._supervisor_service_name), {'code': 1})

            self._set_state(State.STOPPED)
            self._clean_up()
        else:
            log.info('Jormungandr is already stopped.'.format(self.get_name()))

    def start_node(self, reason=''):
        if self._state == State.STOPPED and not self.is_supervisor_node_up():
            self._update_config_if_new()
            self._log_action('start', reason)

            success = self._server.supervisor.startProcess(self._supervisor_service_name)

            if not success:
                raise SupervisorError("Failed to start {}".format(self._supervisor_service_name), {'code': 1})

            log.info("Service {} started.".format(self._supervisor_service_name))

            self._clean_up()
            self._set_state(State.BOOTSTRAPPING)
            self._bootstrap_started_at_time = datetime.now()
        else:
            log.info("Service {} is already started.".format(self.get_name()))

    def restart(self, reason=''):
        self.stop_node(reason)
        self.start_node(reason)

    def is_leader(self):
        if self._leaders != None:
            if len(self._leaders) > 0:
                return True
        return False

    def get_leaders_logs(self):
        if self._state != State.STARTED:
            return

        command = [self._jcli, "rest", "v0", "leaders", "logs", "get", "-h", self._host, "--output-format", "json"]
        proc = Popen(command, stdout=PIPE, stderr=PIPE)

        leaders = None
        stdout, stderr = proc.communicate()
        if proc.returncode != 0:
            raise JcliError('Could not get leaders.', err = {'proc_ret_code': proc.returncode, 'err_code': 1, 'stdout': stdout.decode(), 'stderr': stderr.decode()})

        slots_assigned = json.loads(stdout.decode())
        current_epoch = self.get_current_epoch()
        slots_assigned_filtered = []
        for slot in slots_assigned:
            if slot['scheduled_at_date'].split('.')[0] == current_epoch and slot['finished_at_time'] == None:
                slots_assigned_filtered.append(slot)

        return slots_assigned_filtered

    # get leaders of the node - only executes command if refresh interval is met otherwise returns cached value
    def get_leaders(self):
        if (self._last_time_check_leaders != None):
             if not (datetime.now() - self._last_time_check_leaders).seconds > self._check_leaders_refresh_interval:
                return self._leaders

        return self._get_leaders()

    def unregister_leader(self, id):
        threadLock.acquire()
        if self.get_state() != State.STARTED:
            threadLock.release()
            return

        command = [self._jcli, "rest", "v0", "leaders", "delete", str(id), "-h", self._host]
        proc = Popen(command, stdout=PIPE, stderr=PIPE)
        stdout, stderr = proc.communicate()
        if proc.returncode != 0:
            threadLock.release()
            raise JcliError('An error occurred while deleting leader', err = {'proc_ret_code': proc.returncode, 'err_code': 1, 'stdout': stdout.decode(), 'stderr': stderr.decode()})
        
        lines = stdout.decode()

        if stdout.decode().lower().find('success') == -1:
            threadLock.release()
            raise JcliError('An error occurred while unregistering node leader {}'.format(self.get_name()), err = {'proc_ret_code': proc.returncode, 'err_code': 1, 'stdout': stdout.decode(), 'stderr': stderr.decode()})

        threadLock.release()
        self._get_leaders()
        log.debug("Unregistered leader {}".format(self.get_name()))

    def register_leader(self):
        threadLock.acquire()
        if self.get_state() != State.STARTED:
            threadLock.release()
            return

        command = [self._jcli, "rest", "v0", "leaders", "post", "-f", self._leader_secret_file, "-h", self._host]
        proc = Popen(command, stdout=PIPE, stderr=PIPE)
        stdout, stderr = proc.communicate()
        if proc.returncode != 0:
            threadLock.release()
            raise JcliError('An error occurred while registering node {} as leader.'.format(self.get_name()), err = {'proc_ret_code': proc.returncode, 'err_code': 1, 'stdout': stdout.decode(), 'stderr': stderr.decode()})

        threadLock.release()
        leaders = self._get_leaders()
        if leaders != None:
            log.debug("Found registered leader(s): {}".format(len(leaders)))
        if leaders != None and len(leaders) == 0:
            raise JcliError('Register leader succeeded but leader cannot be found.')

        log.info("Registered node {} as leader.".format(self.get_name()))

        return stdout.decode().strip()

    def run(self):
        log.info("Started thread {}".format(self._node_name))
        while(True):
            try:
                self._update_config_if_new()
                self._get_stats()
                if not self._default_peers_enabled and not self._jormungandr_nodes:
                    self.switch_to_default_peers_bootstrap()
                elif self._default_peers_enabled and self._jormungandr_nodes:
                    self.switch_to_fast_bootstrap()
            except JcliError as e:
                e.print_error()
                if e._errors['err_code'] == JError.FAILED_REST_REQUEST or e._errors['err_code'] == JError.ADDRESS_ALREADY_IN_USE:
                    self.stop_node()
            finally:
                time.sleep(self._refresh_interval)