import json
from copy import copy, deepcopy
from logging import getLogger
import os
import time
import utils

log = getLogger(utils.get_module_name(os.path.basename(__file__)))

class Configurations():
    def __init__(self, parsed_params):
        self._jmanager_config = parsed_params['jmanager_config']
        self._template_config = parsed_params['config_template']

        self._load()

    def _fillTemplate(self, template, obj):
        if type(obj) is list:
            for idx in range(len(obj)):
                if len(template) < len(obj):
                    template.append(obj[idx])
                self._fillTemplate(template[idx], obj[idx])
        elif type(obj) is dict:
            for key in obj.keys():
                if ((not type(obj[key]) is dict) and (not type(obj[key]) is list)):
                    template[key] = obj[key]
                else:
                    self._fillTemplate(template[key], obj[key])

    def _create(self):
        template_data = None
        with open(self._template_config, 'r') as json_file:
            template_data = json.load(json_file)

        with open(self._jmanager_config, 'r') as json_file:
            self._config = json.load(json_file)

        for cfg in self._config["nodes_config"]:
            inst_cfg = deepcopy(template_data)
            node_name = cfg['node_name']
            jmanager_settings = cfg['jmanager_settings']
            config = cfg['config']

            self._fillTemplate(inst_cfg, config)
            config_filename = "{}/{}.json".format(jmanager_settings['node_path'], node_name)
            self._node_configurations.append({
                'node_name': node_name,
                'filename': config_filename,
                'config': inst_cfg, 
                'jmanager_settings': jmanager_settings,
                'common_config_jormungandr': self._config["common_config"]["jormungandr"]
                })

        log.debug('Created {} configurations.'.format(len(self._node_configurations)))

    def _load(self):
        self._node_configurations = []
        self._config = None
        self._create()

        self._last_template_config_check = time.time()
        self._last_jmanager_config_check = time.time()

    def _get_last_modified_time(self, file_path):
        return os.path.getmtime(file_path)

    def _is_new_config_available(self):
        if (self._get_last_modified_time(self._template_config) > self._last_template_config_check 
            or self._get_last_modified_time(self._jmanager_config) > self._last_jmanager_config_check):
            return True
        else:
            False

    def get_latest_config_timestamp(self):
        return max(self._last_template_config_check, self._last_jmanager_config_check)

    def is_config_update_needed(self, last_updated):
        if self._is_new_config_available():
            self._load()
        if last_updated == None or self.get_latest_config_timestamp() > last_updated:
            return True
        else:
            return False

    def get_config(self, node_name):
        for node_config in self._node_configurations:
            if (node_name == node_config['node_name']):
                return node_config
        return None

    def get_config_manager(self):
        return {
            'manager': self._config["common_config"]["manager"],
            'nodes': self._node_configurations
            }

    def get_config_email(self):
        return self._config["common_config"]["email"]

    def get_config_pool_tool(self):
        return self._config["common_config"]["pooltool"]
