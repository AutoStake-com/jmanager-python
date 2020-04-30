#!/usr/bin/env python3

from subprocess import Popen, PIPE
import time
import threading
import sys
import os
import getopt
import traceback
from logging import getLogger
from settings import *
from manager import Manager
from jormungandr import Jormungandr
from configurations import Configurations
from error_types import *
import utils

log = getLogger(utils.get_module_name(os.path.basename(__file__)))

def show_invalid_params(invalid_params, params):
    print("Error: Invalid parameters:")
    for param in invalid_params:
        print("{invalid_param} = {param_value}".format(invalid_param=param, param_value=params[param]))

    print()

def show_help(program_name, params):
    print("Usage: {} -j <jmanager-cfg-path> -t <template-cfg-path>".format(program_name))
    print()
    print("Mandatory parameters:")
    print("{:<4} {:<40} {}".format("-j", "--jmanager-config=JSON_CONFIG", "Main jmanager configuration file. Default is jmanager_config.json."))
    print("{:<4} {:<40} {}".format("-t", "--config-template=JSON_TEMPLATE", "This is node config file template. Values can be overwritten"))
    print("{:<4} {:<40} {}".format("", "", "in jmanager configuration. Default config file is template_config.json."))

def parse_cmd_parameters():
    # default parameters values
    parsed_params = {
        'jmanager_config': 'jmanager_config.json',
        'config_template': 'config_template.json',
    }

    # get program name
    program_name = sys.argv[0]

    argvs = sys.argv[1:]

    try:
        opts, args = getopt.getopt(argvs, "h:j:t:", ["help", "jmanager-config=", "config-template="])
    except getopt.GetoptError:
        show_help(program_name, parsed_params)
        sys.exit(1)

    invalid_params = []
    for opt, arg in opts:
        if opt in ("-h", "--help"):
            show_help(program_name, parsed_params)
            sys.exit(0)
        elif opt in ("-j", "--jmanager-config"):
            if not arg is None and len(arg) > 0:
                parsed_params['jmanager_config'] = arg
            else:
                invalid_params.append('jmanager_config')
        elif opt in ("-t", "--config-template"):
            if not arg is None and len(arg) > 0:
                parsed_params['config_template'] = arg
            else:
                invalid_params.append('config_template')

    if len(invalid_params) > 0:
        show_invalid_params(invalid_params, parsed_params)
        show_help(program_name, parsed_params)
        sys.exit(1)

    return parsed_params

def create_logs_path():
    logpath = os.path.join(BASE_DIR, 'logs')
    if not os.path.exists(logpath):
        log.info("Key directory doesn't exist. Making the directory ...")
        try:
            os.mkdir(logpath)
        except Exception as e:
            log.error('Error: Failed to create dir {}'.format(logpath))
            log.error('Exception occured', exc_info=True)
            sys.exit(1)

if __name__ == "__main__":
    create_logs_path()

    parsed_params = parse_cmd_parameters()

    config = Configurations(parsed_params)

    manager = Manager(config)
    manager.start()