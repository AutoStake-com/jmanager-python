import logging.config
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'simple': {
            'format': '[%(asctime)s.%(msecs)03d] [%(threadName)s] %(levelname)s %(message)s',
            'datefmt': '%Y.%m.%d %H:%M:%S'
        },
        'verbose': {
            'format': '[%(asctime)s.%(msecs)03d] %(levelname)s [%(threadName)s][%(name)s.%(funcName)s:%(lineno)d] %(message)s',
            'datefmt': '%Y.%m.%d %H:%M:%S'
        },
    },
    'handlers': {
        'file': {
            'level': 'DEBUG',
            'class': 'logging.handlers.RotatingFileHandler',
            'formatter': 'verbose',
            'filename': os.path.join(BASE_DIR, 'logs', 'jmanager.log'),
            'maxBytes': 5*1024*1024,
            'backupCount': 10,
            'delay': 0
        },
    },
    'loggers': {
        'jmanager': {
            'handlers': ['file'],
            'level': 'DEBUG',
            'propagate': True,
        },
        'jormungandr': {
            'handlers': ['file'],
            'level': 'DEBUG',
            'propagate': True,
        },
        'manager': {
            'handlers': ['file'],
            'level': 'DEBUG',
            'propagate': True,
        },
        'pool_tool': {
            'handlers': ['file'],
            'level': 'DEBUG',
            'propagate': True,
        },
        'jm_email': {
            'handlers': ['file'],
            'level': 'DEBUG',
            'propagate': True,
        },        
        'configurations': {
            'handlers': ['file'],
            'level': 'DEBUG',
            'propagate': True,
        },
        'error_types': {
            'handlers': ['file'],
            'level': 'DEBUG',
            'propagate': True,
        },
        'slots': {
            'handlers': ['file'],
            'level': 'DEBUG',
            'propagate': True,
        },
    },
}
logging.config.dictConfig(LOGGING)
