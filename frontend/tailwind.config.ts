import type { Config } from 'tailwindcss';

export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        background: 'rgb(var(--background-rgb) / <alpha-value>)',
        'surface-primary': 'rgb(var(--surface-primary-rgb) / <alpha-value>)',
        'surface-secondary': 'rgb(var(--surface-secondary-rgb) / <alpha-value>)',
        'surface-elevated': 'rgb(var(--surface-elevated-rgb) / <alpha-value>)',
        'text-primary': 'rgb(var(--text-primary-rgb) / <alpha-value>)',
        'text-secondary': 'rgb(var(--text-secondary-rgb) / <alpha-value>)',
        'text-tertiary': 'rgb(var(--text-tertiary-rgb) / <alpha-value>)',
        separator: 'var(--separator)',
        'separator-strong': 'var(--separator-strong)',
        accent: 'rgb(var(--hive-accent-rgb) / <alpha-value>)',
        'accent-ink': 'var(--hive-accent-ink)',
        success: 'rgb(var(--success-rgb) / <alpha-value>)',
        warning: 'rgb(var(--warning-rgb) / <alpha-value>)',
        failure: 'rgb(var(--failure-rgb) / <alpha-value>)',
        information: 'rgb(var(--information-rgb) / <alpha-value>)',
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
