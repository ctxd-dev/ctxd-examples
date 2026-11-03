export const messageText = (message) => message.content
  .filter((part) => part.type === 'text').map((part) => part.text).join('\n');

// Only complete pairs enter model context; failed and stopped runs are excluded.
export function recentHistory(messages) {
  const pairs = [];
  for (let i = 0; i < messages.length - 1; i++) {
    const user = messages[i], answer = messages[i + 1];
    const result = answer.metadata?.custom;
    if (user.role === 'user' && answer.role === 'assistant' && result?.done) {
      pairs.push([
        {role: 'user', content: messageText(user).slice(0, 2000)},
        {role: 'assistant', content: result.answer.slice(0, 2000)},
      ]);
    }
  }
  const history = pairs.slice(-2);
  while (history.flat().reduce((n, turn) => n + turn.content.length, 0) > 4000) history.shift();
  return history.flat();
}

export function linkedAnswer(answer, citations = []) {
  let text = answer;
  citations.forEach((citation, index) => {
    if (/^https:\/\/github\.com\/ctxd-dev\/ctxd\/blob\/[a-f0-9]{40}\//.test(citation.url)) {
      text = text.split(citation.label).join(`[${index + 1}](${citation.url})`);
    }
  });
  // A closing Markdown fence must be on its own line, even when the model
  // attaches a verified citation directly after it.
  return text.replace(/^([ \t]*(?:`{3,}|~{3,}))[ \t]*(?=\[\d+\]\(https:\/\/github\.com\/ctxd-dev\/ctxd\/blob\/)/gm, '$1\n\n');
}

export async function* readEvents(response) {
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const message = typeof body.detail === 'string' ? body.detail : 'The request could not be completed.';
    const delay = Number(response.headers.get('retry-after')) || 0;
    yield {type: 'error', message, retry_after: delay};
    return;
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let pending = '';
  try {
    while (true) {
      const {value, done} = await reader.read();
      pending += decoder.decode(value, {stream: !done});
      const lines = pending.split('\n');
      pending = lines.pop();
      for (const line of lines) if (line.trim()) yield JSON.parse(line);
      if (done) {
        if (pending.trim()) yield JSON.parse(pending);
        break;
      }
    }
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}

export async function* revealAnswer(text, signal, {delay = 20, reducedMotion = globalThis.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false} = {}) {
  signal.throwIfAborted();
  if (reducedMotion) {
    yield text;
    return;
  }
  const words = text.match(/\s*\S+\s*/gu) || [text];
  let visible = '';
  for (const word of words) {
    if (visible) {
      await new Promise((resolve, reject) => {
        const cancel = () => { clearTimeout(timer); signal.removeEventListener('abort', cancel); reject(signal.reason); };
        const timer = setTimeout(() => { signal.removeEventListener('abort', cancel); resolve(); }, delay);
        signal.addEventListener('abort', cancel, {once: true});
        if (signal.aborted) cancel();
      });
    }
    signal.throwIfAborted();
    visible += word;
    yield visible;
  }
}

export function chatAdapter(runtimeRef) {
  return {
    async *run({messages, abortSignal}) {
      const question = messageText(messages.at(-1)).trim();
      const item = runtimeRef.current.threads.mainItem;
      if (!item.getState().title) await item.rename(question.slice(0, 54));
      const meta = {progress: 'Connecting…', reads: [], done: false};
      yield {metadata: {custom: {...meta}}};
      try {
        const response = await fetch('/api/ask', {
          method: 'POST', signal: abortSignal,
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({question, history: recentHistory(messages.slice(0, -1))}),
        });
        for await (const event of readEvents(response)) {
          if (event.type === 'progress') meta.progress = event.message;
          if (event.type === 'tool' && event.name === 'read_source' && event.success) {
            meta.reads = [...meta.reads, event.result];
          }
          if (event.type === 'done') {
            const answer = linkedAnswer(event.answer, event.citations);
            meta.revealing = true;
            for await (const text of revealAnswer(answer, abortSignal)) {
              yield {content: [{type: 'text', text}], metadata: {custom: {...meta}}};
            }
            abortSignal.throwIfAborted();
            Object.assign(meta, event, {done: true, revealing: false});
            yield {content: [{type: 'text', text: answer}], metadata: {custom: {...meta}}};
            return;
          }
          if (event.type === 'error') {
            const wait = event.retry_after ? ` Try again in ${event.retry_after} seconds.` : '';
            throw new Error(event.message + wait);
          }
          yield {metadata: {custom: {...meta}}};
        }
        throw new Error('The connection ended before the answer arrived. Please try again.');
      } catch (error) {
        if (abortSignal.aborted) throw error;
        yield {
          metadata: {custom: {...meta, error: error instanceof TypeError ? 'Connection lost. Check your connection and try again.' : error.message}},
          status: {type: 'incomplete', reason: 'error'},
        };
      }
    },
  };
}
