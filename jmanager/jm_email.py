from logging import getLogger
import traceback
import os
import smtplib, ssl
import json
from datetime import datetime
import utils

log = getLogger(utils.get_module_name(os.path.basename(__file__)))

class Email():
    def __init__(self, config):
        self._config = config
        self._config_last_updated = None
        self._update_config_if_new()

    def _update_config_if_new(self):
        if self._config.is_config_update_needed(self._config_last_updated):
            config = self._config.get_config_email()

            self._sender_email = config['sender']
            self._password = config['password']
            self._recipient = config['recipient']
            self._port = config['port']  # 465 for SSL
            self._templates = config['templates']
            self._smtp_server = config['smtp_server']
            self._config_last_updated = self._config.get_latest_config_timestamp()
            
            log.info("Updated email config: {}".format(json.dumps(self._templates, indent=2)))

    def send(self, email_key, data):
        self._update_config_if_new()

        log.debug("Got email params: {} , {}".format(email_key, data))
        # Create a secure SSL context
        context = ssl.create_default_context()
        
        msg = self._templates[email_key]['message']
        if email_key == 'stuck':
            if data.get('timestamp') == None or data.get('timeout') == None or data.get('node_name') == None:
                log.error("Error: Error while trying to send email on 'node stuck'")
                return
            msg = msg.format(timestamp=datetime.now(), timeout=data['timeout'], node_name=data['node_name'])
            log.info("Mail message: {}".format(msg))
        elif email_key == 'bootstrap_restart':
            if data.get('timestamp') is None or data.get('timeout') is None or data.get('node_name') is None:
                log.error("Error: Error while trying to send email on 'boostrap restart'")
                return
            msg = msg.format(timestamp=datetime.now(), timeout=data['timeout'], node_name=data['node_name'])
            log.info("Mail message: {}".format(msg))
        elif email_key == 'leader':
            pass
        elif email_key == 'slots_assigned':
            if data.get('timestamp') is None or data.get('slots') is None or data.get('node_name') is None:
                log.error("Error: Error while trying to send email on 'slots assigned'")
                return
            msg = msg.format(timestamp=data['timestamp'], node_name=data['node_name'], slots=json.dumps(data['slots'], indent=4))
            log.info("Mail message: {}".format(msg))
        else:
            return

        try:
            server = smtplib.SMTP_SSL(self._smtp_server, self._port)
            server.login(self._sender_email, self._password)
            message = """From: {sender}\nSubject: {subject}\n\n
                {msg}""".format(sender=self._sender_email, 
                    subject=self._templates[email_key]['subject'].format(time=datetime.now()),
                    msg=msg)
            server.sendmail(self._sender_email, self._recipient, message)
        except Exception as e:
            log.error('Exception occured', exc_info=True)
        finally:
            if not server is None:
                server.close()