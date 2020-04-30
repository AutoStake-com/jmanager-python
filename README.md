# jmanager
Jormungandr node manager for ITN

Why another jormungandr manager?

It's simple. With the instability of jormungandr node (especially in the first months) we needed a tool to switch between running instances of jormungandr whenever one of the instances went out of sync. At the time we also needed to try many different configurations so we thought to make our own tool for that. Pyhton seemed like a perfect language for such a tool.

## Features 

jmanager does the following:
- keeps nodes on same machine up and running
    - when a node gets out of sync it is restarted
    - if a node crashes it gets restarted (not really tested well)
- support different versions of jormungandr running in parallel
- support different configurations for each running node
- supports running default configurations (used for example when nodes cannot bootstrap from each other if they are all out of sync/down)
- email alerting (with email customizable templates)
- sends tip and slots to pooltool
- after node gets slots assigned it restarts other nodes so they get the leadrs logs schedule too
- uses pooltool for checking if the node is in sync and also compares the running nodes
- simple logging of node restarts for analysis

# General state of jmanager

We are using jmanager for a few months now. So far it's been doing its job but due to lack of resources it has not been thoroughly tested and developed any further. So bugs are most certainly there, waiting to be discovered.

This is by no mean a finished product. Code could be improved further. We have some ideas. However main net around the corner and a Haskell node replacing Jormungandr it makes more sense for us to focus our efforts elsewhere.

# Setting up jmanager

In general to run jmanager you need:
- supervisor
- virtual environment
- Python 3.6 (not tested with any other Python 3.x versions)

In some cases there may be other steps required depending your specific environment.

## Installing prerequisites
### Supervisor

[Supervisor](http://supervisord.org/) is a process control system written in Python. It's meant to be used to control processes related to a project or customer. With some minor changes you could use systemd. 

`sudo apt-get install supervisor`

### Virtual environment

Virtual environment creates isolated environments for Python projects.
sudo apt install virtualenv

### Python

Python programming language
sudo apt-get install python3.6

## jmanager

Clone this repository into a folder on your machine, e.g. jormungandr:

     tiliaio@tilx:~/jormungandr$ git clone https://github.com/tiliaio/jmanager-python.git

Go to the project directory:

    tiliaio@tilx:~/jormungandr$ cd jmanager-python

Create virtual environment into venv directory:
    tiliaio@tilx:~/jormungandr/jmanager-python$ virtualenv -p python3.6 venv

Activate virtual environment:

    tiliaio@tilx:~/jormungandr/jmanager-python$ source venv/bin/activate

Activate virtual environment:

    tiliaio@tilx:~/jormungandr/jmanager-python$ pip install -r requirements.txt

To deactiave a virtual environment:
    tiliaio@tilx:~/jormungandr/jmanager-python$ deactivate

## Putting everything together

### Setting up configuration files
In order for jmanager to work we first need to configure two files in jmanager-python/configs:
- config_template.json
- jmanager_config.json

config_template.json is a template configuration for jormungander. It contains common settings that are used in all jormungandr nodes you are running unless overriden by settings from jmanager_config.json.

jmanager_config.json contains configurations for each of the nodes you are running. The configurations from this file are merged with what you configure in config_template.json and overwrites any duplicate settings.

jmanager_config.json also defines settings needed to run jmanager along with other settings that allow fine tunning the jmanager.

The following steps presume you have 2 jormungandr instances in the following directories:
/home/tiliaio/jormungandr/node_one/
/home/tiliaio/jormungandr/node_two/

You can have more instances (not tested). Each instance can be its own version of jormungandr.

### Creating supervisor configurations

First we need to create configuration files for supervisor in /etc/supervisor/conf.d/:

jmanager.conf:

    [program:jmanager]
    command=/home/tiliaio/jormungandr/jmanager-python/jmanager.sh
    directory=/home/tiliaio/jormungandr/jmanager-python
    autostart=false
    autorestart=false
    environment=PATH="/home/tiliaio/jormungandr:%(ENV_PATH)s"
    user=tiliaio
    stopsignal=SIGUSR1


jnode_one.conf:

    [program:jnode_one]
    command=/home/tiliaio/jormungandr/jgstart.sh node_one
    directory=/home/tiliaio/jormungandr/node_one/
    autostart=false
    autorestart=true
    redirect_stderr=true
    stdout_logfile=/home/tiliaio/jormungandr/node_one/logs/jormungandr.log
    stdout_logfile_maxbytes=20MB
    stdout_logfile_backups=10
    user=tiliaio
    stopsignal=SIGUSR1


jnode_two.conf:

    [program:jnode_two]
    command=/home/tiliaio/jormungandr/jgstart.sh node_two
    directory=/home/tiliaio/jormungandr/node_two/
    autostart=false
    autorestart=false
    redirect_stderr=true
    stdout_logfile=/home/tiliaio/jormungandr/node_two/logs/jormungandr.log
    stdout_logfile_maxbytes=20MB
    stdout_logfile_backups=10
    user=tiliaio
    stopsignal=SIGUSR1

autostart tells supervisor to start the process on boot while autorestart tells it to restart in the event it exits. We want this to be false since it's the job of jmanager to manage the processes. Jmanager could spawn it's own processes instead of having them defined as supervisor processes but supervisor is a general process management tool and could be used without jmanager. We've also defined where jormungandr should store the log files and what conditions need to be met to rotate the logs (when file reaches 20 MB, keep at most 10 files).

Enable REST API for supervisord by adding the following line into /etc/supervisor/supervisord.conf:

    [supervisord]
    [inet_http_server]
    port = 127.0.0.1:9001


### Creating scrpit for starting jormungandr

Jormungandr managed by supervisor gets started by executing the following script (jgstart.sh):

    #!/bin/bash

    ulimit -S 1000000
    ulimit -H 1048576

    if [ $# -eq 0 ]
    then
        echo "No arguments supplied."
        exit 1
    fi

    config_file="$1.json"
    secret_file="../node_secret_TILIA_TILX"
    genesis_hash="$(cat ../genesis_hash)"

    echo $config_file
    echo $secret_file
    echo $genesis_hash

    ./jormungandr --config $config_file --genesis-block-hash $genesis_hash --secret $secret_file &
    jgpid=`echo $!`

    # Stop script
    stop_script() {
        sleep 5
        kill $jgpid
        # give it some time for connections to be cleaned up
        exit 0
    }
    # Wait for supervisor to stop script
    trap stop_script SIGINT SIGTERM SIGUSR1

    while true
    do
        sleep 1
    done


NOTE: Be sure to update the variables (secret_file, genesis_hash). config_file is passed to the script from supervisor.

### Starting jormungandr

Tell supervisor to check for any configuration changes:
`sudo supervisorctl reread`

Enact any changes:
`sudo supervisorctl update`

Check the processes under supervisor's control:
`sudo supervisorctl status`

Start jormungandr to verify everything works:
`sudo supervisorctl start jnode_one`

Start jmanager:
`sudo supervisorctl start jmanager`

Once jmanager is started it will detected that jnode_two is not running and will start it.

