"""One generation request per support answer, using the operator's shared key."""
from __future__ import annotations

import json
import httpx
from agent import INSTRUCTIONS

DEFAULT_MODEL = 'gemini-3.5-flash-lite'
API = 'https://generativelanguage.googleapis.com/v1beta'
MAX_REQUEST_BYTES = 24000


class ModelFailure(Exception):
    def __init__(self, message, *, retry_after=0):
        super().__init__(message)
        self.retry_after = retry_after


def build_request(question, history, reads):
    evidence = [{'path': r['path'], 'start_line': r['start_line'], 'end_line': r['end_line'],
                 'text': '\n'.join(f'{r["start_line"] + n}: {line}' for n, line in enumerate(r['lines']))} for r in reads]
    messages = [{'role': 'model' if h['role'] == 'assistant' else 'user', 'parts': [{'text': h['content']}]} for h in history]
    messages.append({'role': 'user', 'parts': [{'text': json.dumps({'question': question, 'documentation': evidence})}]})
    payload = {'systemInstruction': {'parts': [{'text': INSTRUCTIONS}]}, 'contents': messages,
               'generationConfig': {'maxOutputTokens': 800, 'temperature': 0.2}, 'store': False}
    # Reject rather than silently send an unbounded prompt or remove citation evidence.
    if len(json.dumps(payload).encode()) > MAX_REQUEST_BYTES:
        raise ValueError('The question and conversation are too long. Start a new conversation or shorten the question.')
    return payload


async def generate(payload, *, api_key, model=DEFAULT_MODEL, transport=None):
    async with httpx.AsyncClient(timeout=45, transport=transport, follow_redirects=False) as client:
        try:
            response = await client.post(f'{API}/models/{model}:generateContent',
                                         headers={'x-goog-api-key': api_key}, json=payload)
        except httpx.RequestError:
            raise ModelFailure('The model service is unavailable. Try again later or use the documentation links.') from None
    if response.status_code != 200:
        if response.status_code == 429:
            retry = response.headers.get('retry-after', '60')
            raise ModelFailure('The shared model quota is busy. Please try again later.', retry_after=min(300, max(60, int(retry))) if retry.isdigit() else 60)
        message = {400: 'The model configuration was rejected. The operator needs to check the key and model.',
                   401: 'The assistant credential needs attention from the operator.',
                   403: 'The assistant credential needs attention from the operator.',
                   404: 'The configured model is unavailable.'}.get(response.status_code, 'The model service is unavailable. Please try again later.')
        raise ModelFailure(message)
    try:
        result = response.json()
        candidate = result['candidates'][0]
        answer = '\n'.join(p['text'] for p in candidate['content']['parts'] if 'text' in p and not p.get('thought'))
        if not answer.strip():
            raise ValueError('empty')
        return {'answer': answer, 'finished': candidate.get('finishReason') == 'STOP',
                'tokens': result.get('usageMetadata', {}).get('totalTokenCount', 0)}
    except (ValueError, KeyError, IndexError, TypeError):
        raise ModelFailure('The model returned no usable answer. Try a more specific CTXD question.') from None
