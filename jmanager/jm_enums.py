from enum import Enum

class State(Enum):
    UNKNOWN = 0
    STARTED = 1
    BOOTSTRAPPING = 2
    STOPPED = 3

class JError(Enum):
    UNKNOWN = 0
    FAILED_REST_REQUEST = 1
    ADDRESS_ALREADY_IN_USE = 2