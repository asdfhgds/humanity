import { defineConfig } from 'astro/config';
import tailwind from '@astrojs/tailwind';

export default defineConfig({
  site: process.env.GITHUB_ACTIONS ? 'https://asdfhgds.github.io' : 'https://humanity.example',
  base: process.env.GITHUB_ACTIONS ? '/humanity' : '/',
  integrations: [tailwind()],
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
