import type { Config } from 'tailwindcss';

export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        background: 'var(--background)',
        'surface-primary': 'var(--surface-primary)',
        'surface-secondary': 'var(--surface-secondary)',
        'surface-elevated': 'var(--surface-elevated)',
        'text-primary': 'var(--text-primary)',
        'text-secondary': 'var(--text-secondary)',
        'text-tertiary': 'var(--text-tertiary)',
        separator: 'var(--separator)',
        'separator-strong': 'var(--separator-strong)',
        accent: 'var(--hive-accent)',
        'accent-ink': 'var(--hive-accent-ink)',
        success: 'var(--success)',
        warning: 'var(--warning)',
        failure: 'var(--failure)',
        information: 'var(--information)',
        'w-a': 'var(--w-a)',
        'w-b': 'var(--w-b)',
        'w-c': 'var(--w-c)',
        'w-d': 'var(--w-d)',
        'w-e': 'var(--w-e)',
      },
      borderRadius: {
        control: 'var(--r-control)',
        surface: 'var(--r-surface)',
      },
      transitionTimingFunction: {
        standard: 'cubic-bezier(0.22, 1, 0.36, 1)',
      },
      fontFamily: {
        sans: [
          '-apple-system', 'BlinkMacSystemFont', 'SF Pro Display', 'SF Pro Text',
          'Helvetica Neue', 'Arial', 'sans-serif',
        ],
        technical: ['ui-monospace', 'SF Mono', 'JetBrains Mono', 'Menlo', 'monospace'],
      },
    },
  },
  plugins: [],
} satisfies Config;
