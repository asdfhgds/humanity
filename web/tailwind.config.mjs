/** @type {import('tailwindcss').Config} */
export default {
  content: ['./src/**/*.{astro,html,js,jsx,md,mdx,svelte,ts,tsx,vue}'],
  theme: {
    extend: {
      colors: {
        humanity: {
          dark: '#eee9df',
          darker: '#f5f2eb',
          card: '#fbf9f4',
          border: '#d7d0c5',
          text: '#202725',
          textMuted: '#66635d',
          accent: '#9d4935',
          accentHover: '#783727',
          accentSoft: '#eadbd3',
        },
      },
      fontFamily: {
        sans: ['system-ui', '-apple-system', 'BlinkMacSystemFont', 'Segoe UI', 'sans-serif'],
        serif: ['Iowan Old Style', 'Baskerville', 'Palatino Linotype', 'Book Antiqua', 'Georgia', 'serif'],
        mono: ['SFMono-Regular', 'Consolas', 'Liberation Mono', 'monospace'],
      },
      fontVariantNumeric: {
        tabular: 'tabular-nums',
      },
    },
  },
  plugins: [],
};