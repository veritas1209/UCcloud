#!/usr/bin/python3
"""Short commands for the selected container services; no shell interpolation."""
import os
import sys

COMMANDS = {'nginx':'nginx', 'apache':'httpd', 'postgres':'psql', 'mysql':'mysql',
            'maria':'mariadb', 'mongo':'mongosh', 'redis':'redis-cli', 'tomcat':'catalina.sh'}
if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
    print('사용법: uccloud 서비스 명령옵션\n예: uccloud nginx -h')
    sys.exit(2)
service = sys.argv[1]
os.execvp('sudo', ['sudo', 'docker', 'compose', '-p', 'uccloud', '-f',
                  '/opt/uccloud-services/compose.yml', 'exec', service,
                  COMMANDS[service], *sys.argv[2:]])
