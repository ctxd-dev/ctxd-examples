import {test, expect} from '@playwright/test';

const revision = 'a'.repeat(40);
const source = {label: '[ctxd/README.md:L1-L2]', path: 'ctxd/README.md', start_line: 1, end_line: 2,
  url: `https://github.com/ctxd-dev/ctxd/blob/${revision}/README.md#L1-L2`};
const questions = ['How do I install CTXD?', 'How do I fix a connection?', 'How do I sync context?'];
const answer = 'Start with the official CTXD documentation. ' + 'Read the current source. '.repeat(40) +
  '\n\n```bash\nctxd --version\n```[ctxd/README.md:L1-L2]';

async function configure(page, modelReady = true) {
  await page.route('**/api/config', route => route.fulfill({json: {
    model_ready: modelReady, questions, visitor_window: 5, visitor_day: 20,
  }}));
  await page.route('**/api/status', route => route.fulfill({json: {sync_error: null}}));
  await page.route('**/api/sources', route => route.fulfill({json: {items: [source], revision}}));
  await page.route('**/api/source?*', route => route.fulfill({json: {...source, lines: ['# CTXD', 'Official documentation.']}}));
}

test('path-mounted chat sends suggestions, animates, stops and keeps bounded history', async ({page}) => {
  await configure(page);
  const errors = [], requests = [];
  page.on('pageerror', e => errors.push(e.message));
  let release, cached = false;
  await page.route('**/api/ask', async route => {
    expect(new URL(route.request().url()).pathname).toBe('/support/api/ask');
    requests.push(route.request().postDataJSON());
    await new Promise(resolve => { release = resolve; });
    await route.fulfill({contentType: 'application/x-ndjson', body: JSON.stringify({type: 'done', answer,
      citations: [source], revision, sources_read: 1, elapsed_seconds: 1, cached}) + '\n'}).catch(() => {});
  });
  const fresh = async () => {
    const open = page.getByRole('button', {name: 'Open sidebar', exact: true});
    if (await open.isVisible()) await open.click();
    await page.getByRole('button', {name: 'New chat', exact: true}).click();
    await expect(page.getByRole('heading', {name: 'How can I help with CTXD?'})).toBeVisible();
  };
  await page.goto('./');
  const icon = await page.getAttribute('link[rel="icon"]', 'href');
  expect(icon).toBe('./favicon.svg?v=ctxd');
  const favicon = await page.request.get(new URL(icon, page.url()).href);
  expect(favicon.status()).toBe(200);
  expect(await favicon.text()).toContain('#20C20E');

  await page.getByRole('button', {name: /Get started/}).click();
  await expect.poll(() => requests.length).toBe(1);
  expect(requests[0].question).toBe(questions[0]);
  const stop = page.getByRole('button', {name: 'Stop response', exact: true});
  await expect(stop).toBeVisible();
  await expect(stop).toHaveText('Stop');
  await expect(page.locator('.thinking-dots > span')).toHaveCount(3);
  expect(await page.locator('.thinking-dots > span').first().evaluate(el => getComputedStyle(el).animationName)).toBe('thinking');
  release();
  await expect(page.locator('.response-cursor')).toBeVisible();
  const before = (await page.locator('.markdown').innerText()).length;
  await expect.poll(async () => (await page.locator('.markdown').innerText()).length).toBeGreaterThan(before);
  await stop.click();
  await expect(page.getByText('Response stopped.', {exact: true})).toBeVisible();
  await expect(page.locator('.answer-actions')).toHaveCount(0);

  await page.getByRole('textbox', {name: 'Message CTXD'}).fill('Follow up after stopping');
  await page.getByRole('button', {name: 'Send message', exact: true}).click();
  await expect.poll(() => requests.length).toBe(2);
  expect(requests[1].history).toEqual([]);
  release();
  await expect(page.locator('.answer-actions')).toHaveCount(1, {timeout: 10000});
  await expect(page.locator('.markdown pre')).toContainText('ctxd --version');
  await expect(page.locator('.source-chips a')).toHaveAttribute('href', source.url);

  await fresh(); cached = true;
  await page.getByRole('button', {name: /Fix a connection/}).click();
  await expect.poll(() => requests.length).toBe(3);
  expect(requests[2].question).toBe(questions[1]);
  release();
  await expect(page.locator('.response-cursor')).toBeVisible();
  await expect(page.locator('.answer-info')).toContainText('Saved answer', {timeout: 10000});

  await fresh();
  await page.setViewportSize({width: 390, height: 844});
  await page.locator('.sidebar-brand').getByRole('button', {name: 'Close sidebar'}).click();
  await page.getByRole('button', {name: /Keep context current/}).click();
  await expect.poll(() => requests.length).toBe(4);
  expect(requests[3].question).toBe(questions[2]);
  await expect(stop).toBeInViewport();
  await stop.click(); release();
  await expect(page.getByText('Response stopped.', {exact: true})).toBeVisible();
  expect(errors).toEqual([]);
});

test('documentation works without model credentials and theme remains selectable', async ({page}) => {
  await configure(page, false);
  await page.goto('./');
  await expect(page.getByRole('textbox', {name: 'Message CTXD'})).toBeDisabled();
  await page.getByRole('button', {name: 'Documentation', exact: true}).click();
  await page.locator('.doc-list button').click();
  await expect(page.locator('.source-code')).toContainText('Official documentation.');
  await page.getByRole('button', {name: 'Close panel', exact: true}).click();
  await page.getByRole('button', {name: 'Use light theme', exact: true}).click();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
});
