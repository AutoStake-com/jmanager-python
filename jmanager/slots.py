import json
import requests
import subprocess
import sys
import os
import getopt
import requests
import hashlib
from subprocess import Popen, PIPE
from logging import getLogger
import utils

log = getLogger(utils.get_module_name(os.path.basename(__file__)))

class Slots():
    def __init__(self, config, rest_api_url, pool_id, genesis_hash):
        self._url = rest_api_url 
        self._config = config
        self._node_stats = None
        self._leaders_logs = None
        self._current_epoch = None
        self._previous_epoch = None
        self._pool_id = pool_id
        self._genesis_hash = genesis_hash
        self._headers = {
             "Accept": "application/json",
             "Content-Type": "application/json",
        }
        self._create_path(self._config['send_slots']['key_path'])

    def _get_node_stats(self):
        try:
            r = requests.get("{}/node/stats".format(self._url))
            if r.status_code == 200:
                return r.json()
            else:
                log.error("An error occoured, error code: {}".format(r.status_code))
                return None
        except Exception as e:
            log.error("Error: Nodestats not avaliable. Node is down or rest API misconfigured? Check REST Port and Path.")
            log.error("An exception occured", exc_info=True)
            raise e

        return None

    def _send_data(self, data):
        try:
            log.debug("Packet Sent:")
            log.debug(json.dumps(data))

            r = requests.post(self._config['send_slots']['url'], data=json.dumps(data), headers=self._headers)

            log.debug('Response received:')
            log.debug(r.content.decode())
        except Exception as e:
            log.error('Error: Sending data failed.')
            log.error("An exception occured", exc_info=True)
            raise e

    def _get_leaders_logs(self):
        try:
            r = requests.get("{}/leaders/logs".format(self._url))
            if r.status_code == 200:
                return r.json()
            else:
                log.error("An error occoured, error code: {}".format(r.status_code))
                return None
        except Exception as e:
            log.error('Error: Failed to get leaders logs.')
            log.error("An exception occured", exc_info=True)
            raise e

        return None

    def _write_data(self, filename, data):
        try:
            with open(filename, 'w') as f:
                    f.write(data)
            return True
        except Exception as e:
            log.error('Error: Failed to write data to file {}.'.format(filename))
            log.error("An exception occured", exc_info=True)
            raise e

        return False

    def _read_data(self, filename):
        data = None
        try:
            with open(filename, 'r') as f:
                data = f.read()
        except Exception as e:
            log.error('Error: Failed to read data from file {}.'.format(filename))
            log.error("An exception occured", exc_info=True)
            raise e

        return data

    def _get_current_slots(self):
        current_slots = []
        for slot in self._leaders_logs:
            dot_pos = slot['scheduled_at_date'].find('.')
            if dot_pos > -1 and slot['scheduled_at_date'][0:dot_pos] == str(self._current_epoch):
                current_slots.append(slot)
        return current_slots

    def _generate_new_key(self):
        try:
            cmd = ["openssl", "rand", "-base64", "32"]
            proc = Popen(cmd, stdout=PIPE, stdin=PIPE, stderr=PIPE)
            stdout, stderr = proc.communicate()
            if proc.returncode != 0:
                log.error('Error: Failed to generate new key.')
                log.error('stdout: {}\nstderr:{}'.format(stdout.decode(), stderr.decode()))
                log.error("An exception occured", exc_info=True)

            return stdout.decode().rstrip()
        except Exception as e:
            log.error('Error: Failed to generate new key')
            log.error("An exception occured", exc_info=True)
            raise e

        return None

    def _encrypt_current_slots(self):
        stdout = None
        stderr = None
        try:
            slots_to_encrpyt = json.dumps(self._current_slots) if (len(self._current_slots) > 0) else '[]'
            cmd1 = ["echo", slots_to_encrpyt]
            proc1 = Popen(cmd1, stdout=PIPE, stdin=PIPE, stderr=PIPE)
            cmd2 = ["gpg", "--symmetric", "--armor", "--batch", "--passphrase", self._current_epoch_key]
            proc2 = Popen(cmd2, stdout=PIPE, stdin=proc1.stdout, stderr=PIPE)

            stdout, stderr = proc2.communicate()
            if proc2.returncode != 0:
                log.error('Error: Failed to encrypt current slots.')
                log.error('stdout: {}\nstderr:{}'.format(stdout.decode(), stderr.decode()))
        except Exception as e:
            log.error('Error: Failed to encrypt current slots.')
            log.error("An exception occured", exc_info=True)
            raise e

        return stdout.decode().rstrip()

    def _verify_slots_gpg(self):
        previous_epoch_passphrase_filename = '{key_path}{s}passphrase_{epoch}'.format(key_path=self._config['send_slots']['key_path'], s=os.sep, epoch=self._previous_epoch)
        previous_epoch_key = None
        if os.path.exists(previous_epoch_passphrase_filename):
            previous_epoch_key = self._read_data(previous_epoch_passphrase_filename)
        else:
            previous_epoch_key = ''

        current_epoch_passphrase_filename = '{key_path}{s}passphrase_{epoch}'.format(key_path=self._config['send_slots']['key_path'], s=os.sep, epoch=self._current_epoch)
        if os.path.exists(current_epoch_passphrase_filename):
            self._current_epoch_key = self._read_data(current_epoch_passphrase_filename)
        else:
            self._current_epoch_key = self._generate_new_key()
            self._write_data(current_epoch_passphrase_filename, self._current_epoch_key)

        # Encrypting current slots for sending to pooltool
        current_slots_encrypted = self._encrypt_current_slots()

        data = {
            'currentepoch': str(self._current_epoch),
            'poolid': self._pool_id,
            'genesispref': self._genesis_hash[0:7],
            'userid': self._config['user_id'],
            'assigned_slots': str(len(self._current_slots)),
            'previous_epoch_key': previous_epoch_key,
            'encrypted_slots': current_slots_encrypted
        }

        self._send_data(data)

    def _verify_slots_hash(self):
        # pushing the current slots to file and getting the slots from the last epoch
        leader_slots_prev_epoch_filename = '{key_path}{s}leader_slots_{epoch}'.format(key_path=self._config['send_slots']['key_path'], s=os.sep, epoch=self._previous_epoch)
        last_epoch_slots = None
        if os.path.exists(leader_slots_prev_epoch_filename):
            last_epoch_slots = json.loads(self._read_data(leader_slots_prev_epoch_filename))
        else:
            last_epoch_slots = ''

        leader_slots_current_epoch_filename = '{key_path}{s}leader_slots_{epoch}'.format(key_path=self._config['send_slots']['key_path'], s=os.sep, epoch=self._current_epoch)
        if not os.path.exists(leader_slots_current_epoch_filename):
            self._write_data(leader_slots_current_epoch_filename, json.dumps(self._current_slots))

        # hash verification version
        hash_current_epoch_filename = '{key_path}{s}hash_{epoch}'.format(key_path=self._config['send_slots']['key_path'], s=os.sep, epoch=self._current_epoch)
        current_epoch_hash = hashlib.sha256(json.dumps(self._current_slots).encode('utf-8')).hexdigest()
        self._write_data(hash_current_epoch_filename, current_epoch_hash)

        data = {
            'currentepoch': str(self._current_epoch),
            'poolid': self._pool_id,
            'genesispref': self._genesis_hash[0:7],
            'userid': self._config['user_id'],
            'assigned_slots': str(len(self._current_slots)),
            'this_epoch_hash': current_epoch_hash,
            'last_epoch_slots': '[]' if type(last_epoch_slots) is list and len(last_epoch_slots) == 0 else last_epoch_slots
        }

        self._send_data(data)

    def _no_verification_method(self):
        data = {
            'currentepoch': str(self._current_epoch),
            'poolid': self._pool_id,
            'genesispref': self._genesis_hash[0:7],
            'userid': self._config['user_id'],
            'assigned_slots': str(len(self._current_slots)),
        }

        self._send_data(data)

    def _create_path(self, key_path):
        if not os.path.exists(key_path):
            log.info("Key directory doesn't exist. Making the directory ...")
            try:
                os.mkdir(key_path)
            except Exception as e:
                log.error('Error: Failed to create dir {}'.format(key_path))
                log.error("An exception occured", exc_info=True)
                raise e

    def process(self):
        self._node_stats = self._get_node_stats()
        if self._node_stats is None:
            return
        try:
            self._current_epoch = int(self._node_stats['lastBlockDate'][0 : self._node_stats['lastBlockDate'].find('.')])
            self._previous_epoch = self._current_epoch - 1
        except Exception as e:
            log.error('Error: Failed to parse lastBlockDate.')
            log.error("An exception occured", exc_info=True)
            raise e

        self._leaders_logs = self._get_leaders_logs()
        if self._leaders_logs is None:
            return

        self._current_slots = self._get_current_slots()

        if self._config['send_slots']['verify_slots_gpg'] == 1:
            self._verify_slots_gpg()
        elif self._config['send_slots']['verify_slots_hash'] == 1:
            self._verify_slots_hash()
        else:
            self._no_verification_method()
