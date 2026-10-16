import { defineConfig } from 'astro/config';
import tailwind from '@astrojs/tailwind';

export default defineConfig({
  site: 'https://humanity.example',
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