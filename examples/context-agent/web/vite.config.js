import {defineConfig} from 'vite';
import {readFileSync} from 'node:fs';

export default defineConfig({
  plugins: [{
    name: 'font-license-notices',
    apply: 'build',
    generateBundle: {order: 'post', handler(_options, bundle) {
      // Vite collects JavaScript licenses; these fonts are imported through CSS.
      for (const font of ['inter', 'jetbrains-mono']) {
        const license = readFileSync(new URL(`./node_modules/@fontsource-variable/${font}/LICENSE`, import.meta.url), 'utf8');
        bundle['third-party-notices.txt'].source += `\n\n## ${font} (OFL-1.1)\n\n${license}`;
      }
    }},
  }],
  build: {
    license: {fileName: 'third-party-notices.txt'},
    rolldownOptions: {
      onwarn(warning, defaultHandler) {
        // This application runs entirely in the browser, without React Server Components.
        if (warning.code === 'MODULE_LEVEL_DIRECTIVE' && warning.message.includes('use client')) return;
        defaultHandler(warning);
      },
    },
  },
  server: {proxy: {'/api': 'http://127.0.0.1:8840'}},
});
