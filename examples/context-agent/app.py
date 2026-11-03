"""CTXD support assistant: shared model credentials, bounded retrieval and durable quotas."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import ipaddress
import json
import os
import re
import secrets
import time
from pathlib import Path
from typing import Literal

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from agent import FAQ, PROMPT_VERSION, retrieve, source_url, verified_citations, normalize_citations
from ctxd_context import CtxdContext
from gemini_agent import DEFAULT_MODEL, ModelFailure, build_request, generate
from limits import Budget, Limited, Policy

HERE = Path(__file__).resolve().parent


class Turn(BaseModel):
    role: Literal['user', 'assistant']
    content: str = Field(min_length=1, max_length=2000)


class Question(BaseModel):
    model_config = ConfigDict(extra='forbid')
    question: str = Field(min_length=1, max_length=1200)
    history: list[Turn] = Field(default_factory=list, max_length=4)


def make_context(endpoint, key_file):
    token = key_file.read_text().strip()
    return CtxdContext(endpoint, token)


def create_app(state, endpoint, key_file, *, api_key='', model=DEFAULT_MODEL, policy=Policy(),
               generator=generate, context_factory=None, publication_provider=None, trusted_proxies=(),
               origin_auth_token=''):
    if not re.fullmatch(r'gemini-[a-zA-Z0-9.-]{1,80}', model):
        raise ValueError('Invalid Gemini model identifier')
    app = FastAPI(title='CTXD support assistant', docs_url=None, redoc_url=None, openapi_url=None)
    budget = Budget(state / 'usage.sqlite3', policy)
    app.state.budget = budget
    lock, waiting = asyncio.Lock(), 0
    networks = [ipaddress.ip_network(value) for value in trusted_proxies]

    def context_for():
        return context_factory() if context_factory else make_context(endpoint, key_file)

    def publication():
        if publication_provider:
            return publication_provider()
        response = httpx.get(endpoint + '/example/status', timeout=5,
                             headers={'Authorization': 'Bearer ' + key_file.read_text().strip()})
        response.raise_for_status()
        return response.json()

    def snapshot_for(metadata):
        snapshot = metadata['current']
        if not snapshot or not snapshot['files']:
            raise ValueError('No documentation')
        return snapshot

    def visitor_address(request):
        address = request.client.host if request.client else 'unknown'
        try:
            peer = ipaddress.ip_address(address)
            if any(peer in network for network in networks):
                # Only a specifically trusted Cloudflare tunnel proxy may assert the visitor IP.
                address = str(ipaddress.ip_address(request.headers.get('cf-connecting-ip', address)))
        except ValueError:
            pass
        return address

    @app.middleware('http')
    async def headers(request, call_next):
        if origin_auth_token and request.url.path != '/health':
            supplied = request.headers.get('x-ctxd-origin-token', '')
            if not secrets.compare_digest(supplied.encode(), origin_auth_token.encode()):
                return JSONResponse({'detail': 'Access through the public assistant URL.'}, status_code=403,
                                    headers={'Cache-Control': 'no-store'})
        if request.method == 'POST':
            length = request.headers.get('content-length', '')
            if not length.isdigit() or int(length) > 16000:
                return JSONResponse({'detail': 'Question payload is too large or missing its length.'}, status_code=413)
        response = await call_next(request)
        response.headers.update({'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer',
                                 'X-Content-Type-Options': 'nosniff', 'X-Frame-Options': 'DENY',
                                 'Content-Security-Policy': "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self'; font-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"})
        return response

    @app.get('/')
    def home():
        path = HERE / 'web' / 'dist' / 'index.html'
        if not path.is_file():
            raise HTTPException(503, 'Chat interface is not built. Build the web image or run npm run build in examples/context-agent/web.')
        return FileResponse(path)

    app.mount('/assets', StaticFiles(directory=HERE / 'web' / 'dist' / 'assets', check_dir=False), name='assets')

    @app.get('/favicon.svg')
    def favicon():
        return FileResponse(HERE / 'web' / 'dist' / 'favicon.svg')

    @app.get('/third-party-notices.txt')
    def licenses():
        return FileResponse(HERE / 'web' / 'dist' / 'third-party-notices.txt', media_type='text/plain')

    @app.get('/health')
    def health():
        return {'ok': True}

    @app.get('/api/config')
    def config():
        return {'model_ready': bool(api_key), 'model': model, 'questions': FAQ,
                'visitor_window': policy.visitor_window, 'visitor_day': policy.visitor_day,
                'layout': 'Agent remote from documentation', 'source_host': 'Data container',
                'store_host': 'Data container', 'agent_host': 'Web container'}

    @app.get('/api/status')
    def status():
        try:
            return publication()
        except Exception:
            raise HTTPException(503, 'Documentation service unavailable. Please try again later.')

    @app.get('/api/sources')
    def sources():
        try:
            snapshot = snapshot_for(publication())
            listed = context_for().list_sources()
            return {'items': [{'path': item['path'], 'url': source_url(item['path'], snapshot['revision'])}
                              for item in listed['items'] if item['path'] in snapshot['files']],
                    'complete': listed['complete'], 'revision': snapshot['revision']}
        except Exception:
            raise HTTPException(503, 'Documentation service unavailable. Please try again later.')

    @app.get('/api/source')
    def source(path: str, start_line: int = 1):
        try:
            snapshot = snapshot_for(publication())
            if path not in snapshot['files']:
                raise HTTPException(404, 'Document is not in this collection')
            read = context_for().read_source(path, start_line, start_line + 79)
            if read['content_hash'] != snapshot['files'][path]:
                raise HTTPException(409, 'Documentation is changing. Refresh and try again.')
            read['url'] = source_url(path, snapshot['revision'], read['start_line'], read['end_line'])
            return read
        except HTTPException:
            raise
        except ValueError:
            raise HTTPException(400, 'Invalid line range')
        except Exception:
            raise HTTPException(503, 'Document unavailable. Please refresh and try again.')

    @app.post('/api/ask')
    async def ask(request: Request, body: Question):
        nonlocal waiting
        origin = request.headers.get('origin')
        if origin and origin not in {'http://' + request.headers.get('host', ''), 'https://' + request.headers.get('host', '')}:
            raise HTTPException(403, 'Use the assistant from its own origin')
        if not api_key:
            raise HTTPException(503, 'AI answers are not configured. You can still browse the official documentation.')
        question = body.question.strip()
        if not question:
            raise HTTPException(422, 'Enter a CTXD question')
        history = [turn.model_dump() for turn in body.history]
        if sum(len(turn['content']) for turn in history) > 4000:
            raise HTTPException(422, 'Start a new conversation or shorten the recent messages')
        if waiting >= 4:
            raise HTTPException(429, 'The assistant queue is full. Try again shortly.', headers={'Retry-After': '20'})
        try:
            budget.visitor(visitor_address(request))
        except Limited as exc:
            raise HTTPException(429, str(exc), headers={'Retry-After': str(exc.retry_after)})
        waiting += 1

        async def events():
            nonlocal waiting
            acquired, lease = False, None
            started = time.monotonic()
            try:
                yield json.dumps({'type': 'progress', 'message': 'Waiting for the assistant…'}) + '\n'
                await asyncio.wait_for(lock.acquire(), 20)
                acquired = True
                async with asyncio.timeout(100):
                    metadata = await asyncio.to_thread(publication)
                    snapshot = snapshot_for(metadata)
                    context = context_for()
                    cache_key = hashlib.sha256(json.dumps([question, snapshot['revision'], model, PROMPT_VERSION]).encode()).hexdigest()
                    cacheable = question in FAQ and not history
                    cached = budget.cached(cache_key) if cacheable else None
                    if cached:
                        yield json.dumps({**cached, 'cached': True, 'model_calls': 0, 'tokens': 0,
                                          'elapsed_seconds': round(time.monotonic() - started, 2)}) + '\n'
                        return
                    yield json.dumps({'type': 'progress', 'message': 'Finding relevant documentation…'}) + '\n'
                    reads, activity = await asyncio.to_thread(retrieve, context, question, snapshot, history)
                    for event in activity:
                        yield json.dumps(event) + '\n'
                    if not reads:
                        yield json.dumps({'type': 'error', 'message': 'No relevant documentation was found. Try a more specific CTXD question.'}) + '\n'
                        return
                    payload = build_request(question, history, reads)
                    # Conservative byte-based reservation includes framing/output headroom; no extra model count call.
                    lease = budget.reserve(len(json.dumps(payload).encode()) + 2048)
                    yield json.dumps({'type': 'progress', 'message': 'Writing an answer from the retrieved passages…'}) + '\n'
                    generated = await generator(payload, api_key=api_key, model=model)
                    generated['answer'] = normalize_citations(generated['answer'])
                    citations = verified_citations(generated['answer'], reads)
                    stable = True
                    for read in reads:
                        current = await asyncio.to_thread(context.read_source, read['path'], 1, 1)
                        stable &= current.get('content_hash') == read['content_hash']
                    complete = bool(citations) and stable and generated['finished']
                    result = {'type': 'done', 'answer': generated['answer'], 'citations': citations or [],
                              'status': 'complete' if complete else 'partial', 'revision': snapshot['revision'],
                              'sources_read': len({r['path'] for r in reads}), 'model_calls': 1,
                              'tokens': generated['tokens'], 'cached': False,
                              'elapsed_seconds': round(time.monotonic() - started, 2)}
                    if not complete:
                        result['warning'] = 'Some citations, evidence freshness, or model output could not be verified. Check the sources before using this answer.'
                    if cacheable and complete:
                        budget.cache(cache_key, result)
                    yield json.dumps(result) + '\n'
            except Limited as exc:
                yield json.dumps({'type': 'error', 'message': str(exc), 'retry_after': exc.retry_after}) + '\n'
            except ModelFailure as exc:
                if exc.retry_after:
                    budget.cooldown(exc.retry_after)
                yield json.dumps({'type': 'error', 'message': str(exc), 'retry_after': exc.retry_after}) + '\n'
            except (TimeoutError, httpx.TimeoutException):
                yield json.dumps({'type': 'error', 'message': 'The assistant timed out. Documentation remains available.', 'retry_after': 20}) + '\n'
            except ValueError:
                yield json.dumps({'type': 'error', 'message': 'The documentation changed or the conversation is too long. Refresh or start a new conversation.'}) + '\n'
            except Exception:
                yield json.dumps({'type': 'error', 'message': 'The documentation service is unavailable. Please refresh and try again.'}) + '\n'
            finally:
                if lease:
                    budget.release(lease)
                if acquired:
                    lock.release()
                waiting -= 1
        return StreamingResponse(events(), media_type='application/x-ndjson', headers={'X-Accel-Buffering': 'no'})

    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', type=Path, default=Path('/app-state'))
    parser.add_argument('--endpoint', default='https://data:8850')
    parser.add_argument('--key-file', type=Path, default=Path('/connection/reader.key'))
    parser.add_argument('--host', default='0.0.0.0')
    parser.add_argument('--port', type=int, default=8840)
    args = parser.parse_args()
    def limit(name, default):
        value = int(os.environ.get(name, default))
        if value < 1:
            raise ValueError(f'{name} must be positive')
        return value
    policy = Policy(rpm=limit('MODEL_RPM', 3), rpd=limit('MODEL_RPD', 50), tpm=limit('MODEL_TPM', 60000),
                    visitor_window=limit('VISITOR_10_MIN', 5), visitor_day=limit('VISITOR_DAY', 20))
    app = create_app(args.state, args.endpoint, args.key_file, api_key=os.environ.get('GEMINI_API_KEY', '').strip(),
                     model=os.environ.get('GEMINI_MODEL', DEFAULT_MODEL), policy=policy,
                     trusted_proxies=filter(None, os.environ.get('TRUSTED_PROXY_NETWORKS', '').split(',')),
                     origin_auth_token=os.environ.get('ORIGIN_AUTH_TOKEN', ''))
    uvicorn.run(app, host=args.host, port=args.port, access_log=False, proxy_headers=False)


if __name__ == '__main__':
    main()
