import { defineConfig } from 'astro/config';
import tailwind from '@astrojs/tailwind';
import { readdir, readFile, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

function githubPagesPaths() {
  return {
    name: 'github-pages-paths',
    hooks: {
      'astro:build:done': async ({ dir }) => {
        if (!process.env.GITHUB_ACTIONS) return;

        const visit = async (folder) => {
          const entries = await readdir(folder, { withFileTypes: true });
          await Promise.all(entries.map(async (entry) => {
            const path = join(folder, entry.name);
            if (entry.isDirectory()) return visit(path);
            if (!entry.name.endsWith('.html')) return;

            const html = await readFile(path, 'utf8');
            const rewritten = html
              .replace(/\b(href|src)=(["'])\/(?!humanity(?:\/|\2))/g, '$1=$2/humanity/')
              .replace(/(fetch\(\s*["'`])\/(?!humanity\/)/g, '$1/humanity/')
              .replace(/(window\.location\.href\s*=\s*["'])\/(?!humanity\/)/g, '$1/humanity/');
            if (rewritten !== html) await writeFile(path, rewritten, 'utf8');
          }));
        };

        await visit(fileURLToPath(dir));
      },
    },
  };
}

export default defineConfig({
  site: process.env.GITHUB_ACTIONS ? 'https://asdfhgds.github.io' : 'https://humanity.example',
  base: process.env.GITHUB_ACTIONS ? '/humanity' : '/',
  integrations: [tailwind(), githubPagesPaths()],
  output: 'static',
  build: {
    assets: 'assets',
  },
  vite: {
    ssr: {
      noExternal: ['maplibre-gl'],
    },
  },
});
