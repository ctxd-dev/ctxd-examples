"""Opt-in service isolation, persistence and outage check; no model request is made.

Run on the Docker host as its Docker-authorized account, from the repository root:
python3 examples/context-agent/tests/check_docker.py http://<web-address>:8840
"""
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request

URL = sys.argv[1] if len(sys.argv) > 1 else 'http://127.0.0.1:8840'


def compose(*args, input=None):
    return subprocess.check_output(['docker', 'compose', *args], input=input, text=True)


def get(path):
    try:
        with urllib.request.urlopen(URL + path, timeout=10) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as exc:
        return exc.code, json.load(exc)
    except urllib.error.URLError:
        return 503, {}


def wait():
    for _ in range(90):
        if get('/api/sources')[0] == 200:
            return
        time.sleep(0.5)
    raise AssertionError('Documentation service did not recover')


def check_reads():
    return compose('exec', '-T', 'web', 'python', '-', input='''
import json, httpx
from pathlib import Path
from app import make_context
from agent import FAQ, retrieve
from gemini_agent import build_request
key = Path('/connection/reader.key')
metadata = httpx.get('https://data:8850/example/status', headers={'Authorization': 'Bearer ' + key.read_text().strip()}).json()
snapshot = metadata['current']
context = make_context('https://data:8850', key)
assert set(snapshot['files']) == {item['path'] for item in context.list_sources()['items']}
for path, expected in snapshot['files'].items():
    assert context.read_source(path, 1, 1)['content_hash'] == expected
for question in FAQ:
    reads, _ = retrieve(context, question, snapshot)
    assert reads
    build_request(question, [], reads)
assert not Path('/data/store').exists()
assert not Path('/app/fixtures').exists()
assert not Path('/connection/writer.key').exists()
assert not Path('/app/.env').exists()
print('Verified TLS evidence, bounded FAQ retrieval and source isolation')
''')


try:
    wait()
    print(check_reads().strip())
    compose('exec', '-T', 'web', 'python', '-', input='''
from pathlib import Path
from limits import Budget, Policy, Limited
budget = Budget(Path('/app-state/usage.sqlite3'), Policy(visitor_window=1))
try:
    budget.visitor('docker-persistence-check')
except Limited:
    pass
''')
    compose('restart', 'web')
    wait()
    compose('exec', '-T', 'web', 'python', '-', input='''
from pathlib import Path
from limits import Budget, Policy, Limited
budget = Budget(Path('/app-state/usage.sqlite3'), Policy(visitor_window=1))
try:
    budget.visitor('docker-persistence-check')
except Limited:
    print('Quota persisted across web restart')
else:
    raise AssertionError('Visitor allowance was lost')
''')
    compose('stop', 'data')
    assert get('/health')[0] == 200
    assert get('/api/sources')[0] == 503
    assert get('/api/status')[0] == 503
finally:
    compose('start', 'data')
    wait()
print(check_reads().strip())
print('PASS: CTXD retrieval, quota persistence, data outage and recovery')
