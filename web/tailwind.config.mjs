/** @type {import('tailwindcss').Config} */
export default {
  content: ['./src/**/*.{astro,html,js,jsx,md,mdx,svelte,ts,tsx,vue}'],
  theme: {
    extend: {
      colors: {
        humanity: {
          dark: '#0a0a0a',
          darker: '#050505',
          card: '#141414',
          border: '#2a2a2a',
          text: '#e8e8e8',
          textMuted: '#888888',
          accent: '#e03e3e',
          accentHover: '#c53030',
          accentSoft: '#2d1515',
        },
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
        mono: ['JetBrains Mono', 'monospace'],
      },
    },
  },
  plugins: [],
};