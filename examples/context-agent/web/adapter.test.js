import {test} from 'node:test';
import assert from 'node:assert/strict';
import {recentHistory, linkedAnswer, readEvents, revealAnswer} from './adapter.js';

const user = (text) => ({role: 'user', content: [{type: 'text', text}]});
const answer = (text, done = true) => ({role: 'assistant', content: [{type: 'text', text}], metadata: {custom: {answer: text, done}}});

test('history retains recent completed pairs and excludes failed answers', () => {
  const messages = [user('old'), answer('old answer'), user('failed'), answer('', false), user('recent'), answer('recent answer'), user('new'), answer('new answer')];
  assert.deepEqual(recentHistory(messages).map((m) => m.content), ['recent', 'recent answer', 'new', 'new answer']);
});

test('history honors both per-turn and aggregate API limits without orphaning an answer', () => {
  const messages = [user('x'.repeat(1200)), answer('y'.repeat(5000)), user('z'.repeat(1200)), answer('a'.repeat(5000))];
  const history = recentHistory(messages);
  assert.equal(history.length, 2);
  assert.equal(history[0].content, 'z'.repeat(1200));
  assert.equal(history[1].content.length, 2000);
});

test('NDJSON handles split UTF-8 characters and final lines without a newline', async () => {
  const encoded = new TextEncoder().encode('{"type":"progress","message":"Writing…"}\n{"type":"done","answer":"Done"}');
  const body = new ReadableStream({start(controller) {for (const byte of encoded) controller.enqueue(Uint8Array.of(byte)); controller.close();}});
  const events = [];
  for await (const event of readEvents(new Response(body))) events.push(event);
  assert.equal(events[0].message, 'Writing…');
  assert.equal(events[1].answer, 'Done');
});

test('HTTP quota failures preserve the actionable retry delay', async () => {
  const response = new Response('{"detail":"Busy"}', {status: 429, headers: {'Retry-After': '30'}});
  const events = [];
  for await (const event of readEvents(response)) events.push(event);
  assert.deepEqual(events, [{type: 'error', message: 'Busy', retry_after: 30}]);
});

test('only verified repository citation URLs become numbered links', () => {
  const url = `https://github.com/ctxd-dev/ctxd/blob/${'a'.repeat(40)}/README.md#L1-L3`;
  assert.equal(linkedAnswer('See [ref].', [{label: '[ref]', url}]), `See [1](${url}).`);
  assert.equal(linkedAnswer('See [ref].', [{label: '[ref]', url: 'javascript:alert(1)'}]), 'See [ref].');
});

test('a citation attached to a closing code fence stays outside the code block', () => {
  const url = `https://github.com/ctxd-dev/ctxd/blob/${'a'.repeat(40)}/README.md#L1-L3`;
  const answer = '```bash\nctxd --version\n```[ref]\n\n### Next step';
  assert.equal(linkedAnswer(answer, [{label: '[ref]', url}]), `\`\`\`bash\nctxd --version\n\`\`\`\n\n[1](${url})\n\n### Next step`);
});

test('buffered answers reveal incrementally without changing Markdown or whitespace', async () => {
  const text = '  Hello CTXD.\n\n```js\n  const value = "你好";\n```\n';
  const frames = [];
  for await (const frame of revealAnswer(text, new AbortController().signal, {delay: 0})) frames.push(frame);
  assert.ok(frames.length > 3);
  assert.ok(frames.every((frame) => text.startsWith(frame)));
  assert.equal(frames.at(-1), text);
});

test('stop interrupts an answer during its reveal delay', async () => {
  const controller = new AbortController();
  const reveal = revealAnswer('first second third', controller.signal, {delay: 1000});
  assert.equal((await reveal.next()).value, 'first ');
  const next = reveal.next();
  controller.abort();
  await assert.rejects(next, {name: 'AbortError'});
  assert.equal((await reveal.next()).done, true);
});

test('reduced-motion preference displays the complete answer immediately', async () => {
  const reveal = revealAnswer('first second third', new AbortController().signal, {reducedMotion: true});
  assert.equal((await reveal.next()).value, 'first second third');
  assert.equal((await reveal.next()).done, true);
});
