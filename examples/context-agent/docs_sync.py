"""Publish an immutable revision of CTXD's official public documentation."""
from __future__ import annotations

import fcntl
import json
import re
import subprocess
import time
from pathlib import Path

from context import digest, inventory
from producer import publish

REPOSITORY = "https://github.com/ctxd-dev/ctxd"


def write_json(path, value):
    temporary = path.with_suffix('.pending')
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    temporary.replace(path)


def git(repo, *args):
    return subprocess.check_output(['git', '--git-dir', str(repo), *args], stderr=subprocess.DEVNULL, timeout=45)


def checkout_docs(state: Path, ref='HEAD', repository=REPOSITORY + '.git'):
    repo = state / 'upstream.git'
    if not repo.exists():
        subprocess.run(['git', 'init', '--bare', str(repo)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    git(repo, 'fetch', '--depth=1', '--no-tags', repository, ref)
    revision = git(repo, 'rev-parse', 'FETCH_HEAD^{commit}').decode().strip()
    if not re.fullmatch('[0-9a-f]{40}', revision):
        raise ValueError('Invalid Git revision')
    paths = git(repo, 'ls-tree', '-r', '--name-only', revision).decode().splitlines()
    paths = [p for p in paths if p == 'README.md' or (p.startswith('docs/public/') and p.endswith('.md'))]
    if not 2 <= len(paths) <= 100 or 'docs/public/quickstart.md' not in paths:
        raise ValueError('Expected public documentation not found')
    destination = state / 'sources' / revision
    destination.mkdir(parents=True, exist_ok=True)
    size = 0
    for name in paths:
        if '..' in Path(name).parts or Path(name).is_absolute():
            raise ValueError('Invalid document path')
        mode = git(repo, 'ls-tree', revision, '--', name).decode().split()[0]
        if mode not in {'100644', '100755'}:
            raise ValueError('Only regular documentation files are supported')
        content = git(repo, 'show', f'{revision}:{name}').decode('utf-8')
        size += len(content.encode())
        if size > 2_000_000:
            raise ValueError('Documentation exceeds the example corpus budget')
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding='utf-8')
    inventory(destination)
    return destination, revision


def sync_docs(state, endpoint, *, ref='HEAD', repository=REPOSITORY + '.git'):
    state.mkdir(parents=True, exist_ok=True)
    with (state / 'sync.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        status_path = state / 'publication.json'
        status = json.loads(status_path.read_text()) if status_path.exists() else {}
        try:
            source, revision = checkout_docs(state, ref, repository)
            documents = inventory(source)
            files = {path: digest(text) for path, text in documents.items()}
            # Replay on each successful check also restores a lost or empty store.
            publish(source, endpoint, (state / 'writer.key').read_text().strip(), state / 'manifest.json')
            from ctxd_context import CtxdContext
            context = CtxdContext(endpoint, (state / 'reader.key').read_text().strip())
            for path, expected in files.items():
                if context.read_source(path, 1, 1)['content_hash'] != expected:
                    raise ValueError('Published document verification failed')
            changed = status.get('current', {}).get('revision') != revision
            current = {'revision': revision, 'files': files,
                       'published_at': time.time() if changed else status['current']['published_at']}
            status = {'repository': REPOSITORY, 'current': current,
                      'checked_at': time.time(), 'sync_error': None}
            write_json(status_path, status)
            return status
        except Exception:
            # Preserve the last published identity and never replace it with a failed scan.
            status.update(checked_at=time.time(), sync_error='GitHub synchronization failed; serving the last verified publication.')
            write_json(status_path, status)
            if not status.get('current'):
                raise RuntimeError('Initial documentation synchronization failed') from None
            return status
