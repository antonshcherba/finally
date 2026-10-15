import type { Config } from 'tailwindcss';

export default {
  content: ['./src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        bg: '#0d1117',
        panel: '#161b22',
        line: '#30363d',
        muted: '#8b949e',
        accent: '#ecad0a',
        primary: '#209dd7',
        secondary: '#753991',
        up: '#3fb950',
        down: '#f85149',
      },
      fontFamily: { mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'] },
    },
  },
} satisfies Config;
