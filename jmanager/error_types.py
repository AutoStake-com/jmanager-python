import traceback
import os
from logging import getLogger
import utils

log = getLogger(utils.get_module_name(os.path.basename(__file__)))

class JcliError(Exception):
    def __init__(self, msg, err):
        self._message = msg
        self._errors = err

    def print_error(self):
        log.error(self._message)
        log.error(self._errors)
        log.error('Exception occured', exc_info=True)

class SupervisorError(Exception):
    def __init__(self, msg, err):
        self._message = msg
        self._errors = err

    def print_error(self):
        log.error(self._message)
        log.error(self._errors)
        log.error('Exception occured', exc_info=True)
