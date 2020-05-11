import requests
from requests.exceptions import HTTPError
from datetime import datetime, timedelta
import time
import json
import os
from logging import getLogger
from error_types import *
from slots import Slots
import utils

log = getLogger(utils.get_module_name(os.path.basename(__file__)))

class PoolTool():
    def __init__(self, config):
        self._config = config
        self._config_last_updated = None
        self._update_config_if_new()
        self._platform_name = 'jmanager.py by Tilia IO'
        self._tip_data = None
        self._tip_last_updated = datetime.utcnow()

    def _update_config_if_new(self):
        if self._config.is_config_update_needed(self._config_last_updated):
            self._config_pool_tool = self._config.get_config_pool_tool()
            self._status_summary_last_refresh = None
            self._status_summary = None
            self._refresh_interval = 10
            self._config_last_updated = self._config.get_latest_config_timestamp()

    def _request(self, url):
        try:
            r = requests.get(url)
            if r.status_code == 200:
                return json.loads(r.content.decode())
            else:
                log.error("An error occoured. PoolTool returned code {}".format(r.status_code))
        except Exception as e:
            log.error('Exception occured', exc_info=True)

        return None

    def _get_status_summary(self):
        if self._status_summary_last_refresh is None or (datetime.now() - self._status_summary_last_refresh).seconds > self._config_pool_tool['status_summary']['refresh_rate']:
            self._status_summary = self._request(self._config_pool_tool['status_summary']['url'])
            self._status_summary_last_refresh = datetime.now()
        
        return self._status_summary

    def send_my_tip(self):
        if (self._tip_data == None or
            (datetime.utcnow() - self._tip_last_updated).seconds < self._config_pool_tool['send_tip']['refresh_rate']):
            return

        try:
            log.debug("Packet Sent:")
            log.debug(json.dumps(self._tip_data, indent=2))
            r = requests.get(self._config_pool_tool['send_tip']['url'], params=self._tip_data)
            log.debug('Response received:')
            log.debug(r.content.decode())
            self._tip_last_updated = datetime.utcnow()
        except Exception as e:
            log.error('Exception occured', exc_info=True)

    def refresh_data_for_tip_update(self, stats, last_block, pool_id, genesis_hash):
        if stats == None or last_block == None:
            return

        self._tip_data = {
            "poolid": pool_id,
            "userid": self._config_pool_tool['user_id'],
            "genesispref": genesis_hash,
            "mytip": stats['lastBlockHeight'],
            "lasthash": stats['lastBlockHash'],
            "lastpool": last_block[168:168+64],
            "lastparent": last_block[104:104+64],
            "lastslot": int('0x' + last_block[24:24+8], 16),
            "lastepoch": int('0x' + last_block[16:16+8], 16),
            "jormver": stats['version'],
            "platform": self._platform_name
        }

    def get_max_tip(self):
        return 0 if self._status_summary is None else self._status_summary['majoritymax']
    
    def send_slots(self, rest_api_url, pool_id, genesis_hash):
        slots = Slots(self._config_pool_tool, rest_api_url, pool_id, genesis_hash)
        slots.process()
