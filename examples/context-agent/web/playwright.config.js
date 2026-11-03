import {defineConfig} from '@playwright/test';

const port = Number(process.env.PLAYWRIGHT_PORT || 4173);

export default defineConfig({
  testMatch: 'browser.test.js',
  timeout: 30000,
  use: {
    baseURL: `http://127.0.0.1:${port}/support/`,
    viewport: {width: 1280, height: 900},
  },
  webServer: {
    command: `npx vite preview --host 127.0.0.1 --port ${port} --strictPort --base /support/`,
    url: `http://127.0.0.1:${port}/support/`,
    reuseExistingServer: false,
  },
});
