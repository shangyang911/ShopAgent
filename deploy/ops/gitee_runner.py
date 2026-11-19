#!/usr/bin/env python3
"""Resume the registered Gitee agent as its dedicated unprivileged user."""
import json
import os
from pathlib import Path

home = Path('/home/shopagent-ci')
configurations = list((home / '.gitee-go-agent').glob('*.conf'))
if len(configurations) != 1:
    raise SystemExit('Expected exactly one registered ShopAgent Gitee agent')
settings = json.loads(configurations[0].read_text())
binary = str(home / 'gitee-go-runner')
os.execv(binary, [binary, 'start', '-t', settings['uuid'], '-u', settings['server_url'],
                 '-l', 'info', '-g', '-c', '1'])
