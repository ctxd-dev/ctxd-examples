"""Support generation, citations, quotas, cancellation and credential isolation."""
import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from agent import FAQ, retrieve, verified_citations
from app import create_app
from context import digest, inventory
from test_context import deployment
from data_service import create_data_app
from gemini_agent import ModelFailure, build_request, generate
from limits import Budget, Limited, Policy
from producer import initialize_credentials, publish


@pytest.fixture
def corpus(tmp_path, deployment):
    source = tmp_path / 'docs-source'
    docs = source / 'docs/public'
    docs.mkdir(parents=True)
    (source / 'README.md').write_text('# CTXD\nOfficial docs.\n')
    (docs / 'quickstart.md').write_text('# CTXD setup\n\npip install ctxd\nctxd endpoint status\nConnect an agent via MCP.\n')
    (docs / 'ctxfs.md').write_text('# Authentication\n\nNetwork authorization requires a Bearer credential.\nThe local socket uses peer trust.\n')
    publish(source, deployment['endpoint'], deployment['writer'], deployment['state'] / 'manifest.json')
    snapshot = {'revision': 'a' * 40, 'files': {p: digest(t) for p, t in inventory(source).items()}, 'published_at': 123}
    return deployment['ctxd'], snapshot


def make_app(tmp_path, corpus, generator=None, **kwargs):
    provider, snapshot = corpus
    async def default_generator(payload, *, api_key, model):
        assert api_key == 'server-secret'
        return {'answer': 'Install CTXD with pip. [ctxd/docs/public/quickstart.md:L3-L4]', 'finished': True, 'tokens': 100}
    return create_app(tmp_path, 'http://unused', tmp_path / 'reader.key', api_key='server-secret',
                      generator=generator or default_generator,
                      context_factory=lambda: provider,
                      publication_provider=lambda: {'current': snapshot}, **kwargs)


def events(response):
    assert response.status_code == 200
    return [json.loads(line) for line in response.text.splitlines()]


def test_single_model_request_never_exposes_credentials(corpus):
    provider, snapshot = corpus
    reads, _ = retrieve(provider, FAQ[0], snapshot)
    payload = build_request(FAQ[0], [], reads)
    calls = []
    def handler(request):
        calls.append(request)
        assert request.headers['x-goog-api-key'] == 'test-secret'
        assert 'test-secret' not in str(request.url) + request.content.decode()
        assert json.loads(request.content)['store'] is False
        return httpx.Response(200, json={'candidates': [{'finishReason': 'STOP', 'content': {'parts': [{'text': 'Install CTXD. [ctxd/docs/public/quickstart.md:L3-L4]'}]}}], 'usageMetadata': {'totalTokenCount': 42}})
    result = asyncio.run(generate(payload, api_key='test-secret', transport=httpx.MockTransport(handler)))
    assert len(calls) == 1 and result['finished'] and result['tokens'] == 42
    assert verified_citations(result['answer'], reads)
    assert 'test-secret' not in json.dumps(result)


@pytest.mark.parametrize('code', [400, 401, 403, 404, 429, 500])
def test_provider_errors_are_sanitized_without_retry(code):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(code, text='test-secret')
    with pytest.raises(ModelFailure) as error:
        asyncio.run(generate({}, api_key='test-secret', transport=httpx.MockTransport(handler)))
    assert 'test-secret' not in str(error.value) and len(calls) == 1
    assert bool(error.value.retry_after) == (code == 429)


def test_citations_reject_unread_and_malformed_ranges(corpus):
    reads, _ = retrieve(corpus[0], FAQ[0], corpus[1])
    for answer in ['Uncited answer', '[ctxd/docs/public/quickstart.md:L90-L91]', '[ctxd/docs/public/quickstart.md:L3-L4] [ctxd/bogus]']:
        assert verified_citations(answer, reads) is None
    assert verified_citations('[ctxd/docs/public/quickstart.md:L3-L4]', reads)


def test_prompt_budget_rejects_oversized_context(corpus):
    reads, _ = retrieve(corpus[0], FAQ[0], corpus[1])
    with pytest.raises(ValueError):
        build_request('x' * 30000, [], reads)


def test_global_key_cannot_be_overridden_and_cache_uses_revision(tmp_path, corpus):
    client = TestClient(make_app(tmp_path, corpus))
    body = {'question': FAQ[0]}
    first = events(client.post('/api/ask', json=body, headers={'X-Model-Key': 'attacker-key'}))[-1]
    assert first['status'] == 'complete' and first['cached'] is False
    assert first['citations'][0]['url'].startswith('https://github.com/ctxd-dev/ctxd/blob/' + 'a' * 40)
    second = events(client.post('/api/ask', json=body))[-1]
    assert second['cached'] is True and second['model_calls'] == 0 and second['tokens'] == 0
    corpus[1]['revision'] = 'b' * 40
    assert events(client.post('/api/ask', json=body))[-1]['cached'] is False
    assert client.post('/api/ask', json={**body, 'model': 'some-other-model'}).status_code == 422
    assert 'server-secret' not in client.get('/api/config').text + json.dumps(first)
    assert client.get('/').headers['cache-control'] == 'no-store'


def test_unconfigured_assistant_keeps_docs_available(tmp_path, corpus):
    app = create_app(tmp_path, 'http://unused', tmp_path / 'reader.key', context_factory=lambda: corpus[0],
                     publication_provider=lambda: {'current': corpus[1]})
    client = TestClient(app)
    assert client.get('/api/config').json()['model_ready'] is False
    assert client.get('/api/sources').status_code == 200
    assert client.get('/api/source', params={'path': 'ctxd/docs/public/quickstart.md'}).json()['lines']
    assert client.get('/api/source', params={'path': '../secret'}).status_code == 404
    assert client.post('/api/ask', json={'question': FAQ[0]}).status_code == 503


def test_failed_retrieval_never_calls_model(tmp_path, corpus):
    corpus[1]['files']['ctxd/docs/public/quickstart.md'] = 'invalid-hash'
    async def forbidden(*args, **kwargs):
        pytest.fail('Model must not be called')
    client = TestClient(make_app(tmp_path, corpus, forbidden))
    assert events(client.post('/api/ask', json={'question': FAQ[0]}))[-1]['type'] == 'error'
    with client.app.state.budget.connect() as db:
        assert db.execute('SELECT count(*) FROM calls').fetchone()[0] == 0


def test_upstream_429_cools_down_shared_key(tmp_path, corpus):
    count = 0
    async def generator(*args, **kwargs):
        nonlocal count
        count += 1
        raise ModelFailure('Quota exhausted', retry_after=90)
    client = TestClient(make_app(tmp_path, corpus, generator))
    for _ in range(2):
        result = events(client.post('/api/ask', json={'question': FAQ[0]}))[-1]
        assert result['type'] == 'error' and result['retry_after'] > 0
    assert count == 1


def test_usage_survives_restarts_and_does_not_store_raw_ips(tmp_path):
    now = [100000]
    policy = Policy(visitor_window=2, visitor_day=3, rpm=2, rpd=3, tpm=100)
    database = tmp_path / 'limits.sqlite3'
    budget = Budget(database, policy, lambda: now[0])
    budget.visitor('198.51.100.7'); budget.visitor('198.51.100.7')
    budget = Budget(database, policy, lambda: now[0])
    with pytest.raises(Limited): budget.visitor('198.51.100.7')
    lease = budget.reserve(60)
    with pytest.raises(Limited): budget.reserve(1)
    budget.release(lease)
    with pytest.raises(Limited): budget.reserve(50)
    budget.release('not-the-lease')
    now[0] += 61
    lease = budget.reserve(60); budget.release(lease)
    now[0] += 600
    budget.visitor('198.51.100.7')
    with pytest.raises(Limited): budget.visitor('198.51.100.7')
    lease = budget.reserve(60); budget.release(lease)
    now[0] += 61
    with pytest.raises(Limited): budget.reserve(1)
    with budget.connect() as db:
        assert db.execute('SELECT count(*) FROM calls').fetchone()[0] == 3
        assert '198.51.100.7' not in str(db.execute('SELECT * FROM visitors').fetchall())


def test_simultaneous_requests_cannot_overdraw_allowance(tmp_path):
    budget = Budget(tmp_path / 'limits.sqlite3', Policy(visitor_window=2))
    def attempt(_):
        try:
            budget.visitor('same-client')
            return True
        except Limited:
            return False
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(attempt, range(8))) == 2


def test_forged_forwarding_headers_do_not_bypass_limits(tmp_path, corpus):
    client = TestClient(make_app(tmp_path, corpus, policy=Policy(visitor_window=1)))
    assert events(client.post('/api/ask', json={'question': FAQ[0]}))[-1]['type'] == 'done'
    response = client.post('/api/ask', json={'question': FAQ[0]}, headers={'CF-Connecting-IP': '1.2.3.4', 'X-Forwarded-For': '1.2.3.4'})
    assert response.status_code == 429 and int(response.headers['retry-after']) > 0


def test_cancellation_closes_provider_request():
    async def check():
        started, cancelled = asyncio.Event(), asyncio.Event()
        async def handler(request):
            started.set()
            try: await asyncio.sleep(30)
            finally: cancelled.set()
        task = asyncio.create_task(generate({}, api_key='secret', transport=httpx.MockTransport(handler)))
        await started.wait(); task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
        assert cancelled.is_set()
    asyncio.run(check())


def test_publication_status_enforces_reader_capability(tmp_path, corpus):
    initialize_credentials(tmp_path)
    (tmp_path / 'publication.json').write_text(json.dumps({'current': corpus[1]}))
    client = TestClient(create_data_app(tmp_path))
    assert client.get('/example/status').status_code == 401
    writer = {'Authorization': 'Bearer ' + (tmp_path / 'writer.key').read_text()}
    assert client.get('/example/status', headers=writer).status_code == 403
    reader = {'Authorization': 'Bearer ' + (tmp_path / 'reader.key').read_text()}
    assert client.get('/example/status', headers=reader).json() == {'current': corpus[1]}
    assert client.post('/example/baseline', json={}, headers=reader).status_code == 404


@pytest.mark.parametrize('visitor', ['198.51.100.7', '2001:db8::7'])
def test_cloudflare_client_ip_has_its_own_allowance(tmp_path, corpus, visitor):
    client = TestClient(make_app(tmp_path, corpus, policy=Policy(visitor_window=1),
                                trusted_proxies=['10.0.0.2/32']), client=('10.0.0.2', 50000))
    body = {'question': FAQ[0]}
    assert events(client.post('/api/ask', json=body, headers={'CF-Connecting-IP': visitor}))[-1]['type'] == 'done'
    assert events(client.post('/api/ask', json=body, headers={'CF-Connecting-IP': '198.51.100.8'}))[-1]['type'] == 'done'
    assert client.post('/api/ask', json=body, headers={'CF-Connecting-IP': visitor, 'X-Forwarded-For': '198.51.100.9'}).status_code == 429


def test_client_disconnect_releases_generation_lease(tmp_path, corpus):
    async def check():
        started, cancelled = asyncio.Event(), asyncio.Event()
        async def generator(*args, **kwargs):
            started.set()
            try:
                await asyncio.sleep(30)
            finally:
                cancelled.set()
        app = make_app(tmp_path, corpus, generator)
        body = json.dumps({'question': FAQ[0]}).encode()
        sent_body = False
        async def receive():
            nonlocal sent_body
            if not sent_body:
                sent_body = True
                return {'type': 'http.request', 'body': body, 'more_body': False}
            await started.wait()
            return {'type': 'http.disconnect'}
        async def send(message):
            pass
        scope = {'type': 'http', 'asgi': {'version': '3.0', 'spec_version': '2.3'}, 'http_version': '1.1',
                 'method': 'POST', 'scheme': 'http', 'path': '/api/ask', 'raw_path': b'/api/ask',
                 'query_string': b'', 'root_path': '', 'headers': [(b'host', b'test'), (b'content-type', b'application/json'), (b'content-length', str(len(body)).encode())],
                 'client': ('127.0.0.1', 1234), 'server': ('test', 80)}
        await asyncio.wait_for(app(scope, receive, send), 5)
        assert cancelled.is_set()
        lease = app.state.budget.reserve(1)
        app.state.budget.release(lease)
    asyncio.run(check())


def test_combined_citations_are_normalized_then_verified(corpus):
    from agent import normalize_citations
    reads, _ = retrieve(corpus[0], FAQ[0], corpus[1])
    raw = '[ctxd/docs/public/quickstart.md:L3-L3, ctxd/docs/public/quickstart.md:L4-L4]'
    answer = normalize_citations(raw)
    assert answer == '[ctxd/docs/public/quickstart.md:L3-L3] [ctxd/docs/public/quickstart.md:L4-L4]'
    assert len(verified_citations(answer, reads)) == 2
    assert verified_citations(normalize_citations('[ctxd/docs/public/quickstart.md:L3, ctxd/docs/public/missing.md:L4]'), reads) is None
