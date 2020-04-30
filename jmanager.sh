#!/bin/bash

source venv/bin/activate
jmanager/jmanager.py -j configs/jmanager_config_prod.json -t configs/config_template_prod.json &
jgpid=`echo $!`

# Stop script
stop_script() {
    sleep 5
    kill $jgpid
    exit 0
}
# Wait for supervisor to stop script
trap stop_script SIGINT SIGTERM SIGUSR1

while true
do
    sleep 1
done
