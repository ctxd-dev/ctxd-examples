import json
import subprocess

from test_context import deployment
from docs_sync import sync_docs


def commit(repo):
    subprocess.run(['git', '-C', str(repo), 'add', '.'], check=True, capture_output=True)
    subprocess.run(['git', '-C', str(repo), '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', '-c', 'commit.gpgsign=false', 'commit', '-m', 'Fixture'], check=True, capture_output=True)


def test_github_publication_updates_removes_and_survives_outage(deployment, tmp_path):
    d = deployment
    repo = tmp_path / 'upstream'
    repo.mkdir()
    subprocess.run(['git', 'init', str(repo)], check=True, capture_output=True)
    docs = repo / 'docs/public'; docs.mkdir(parents=True)
    (repo / 'README.md').write_text('# CTXD\nPublic docs\n')
    (docs / 'quickstart.md').write_text('# Setup\nStart the server.\n')
    (docs / 'old.md').write_text('# Old page\nOld guidance.\n')
    commit(repo)
    first = sync_docs(d['state'], d['endpoint'], repository=str(repo))
    (docs / 'quickstart.md').write_text('# Setup\nStart the context service.\n')
    (docs / 'old.md').unlink(); (docs / 'new.md').write_text('# New page\nNew guidance.\n')
    commit(repo)
    second = sync_docs(d['state'], d['endpoint'], repository=str(repo))
    assert first['current']['revision'] != second['current']['revision']
    assert d['ctxd'].read_source('ctxd/docs/public/quickstart.md')['lines'][1] == 'Start the context service.'
    assert 'ctxd/docs/public/old.md' not in {x['path'] for x in d['ctxd'].list_sources()['items']}
    failed = sync_docs(d['state'], d['endpoint'], repository=str(tmp_path / 'missing-repo'))
    assert failed['current'] == second['current'] and failed['sync_error']
    assert d['ctxd'].read_source('ctxd/docs/public/new.md')['lines']
    replay = sync_docs(d['state'], d['endpoint'], repository=str(repo))
    assert replay['current'] == second['current'] and replay['sync_error'] is None
    # An empty or unexpected upstream layout must not withdraw the last published docs.
    (docs / 'quickstart.md').unlink(); commit(repo)
    assert sync_docs(d['state'], d['endpoint'], repository=str(repo))['sync_error']
    assert d['ctxd'].read_source('ctxd/docs/public/quickstart.md')['lines']
