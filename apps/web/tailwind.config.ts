import type { Config } from 'tailwindcss';

/**
 * Design tokens from Doc 06 section 6.4. Colours are declared as CSS custom
 * properties in globals.css and referenced here, so a theme change is one file
 * and never a find-and-replace across components.
 */
export default {
  content: ['./app/**/*.{ts,tsx}', './components/**/*.{ts,tsx}', './features/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        base: 'var(--bg-base)',
        elevated: 'var(--bg-elevated)',
        glass: 'var(--bg-glass)',
        line: 'var(--border-subtle)',
        'line-strong': 'var(--border-strong)',
        primary: 'var(--text-primary)',
        secondary: 'var(--text-secondary)',
        tertiary: 'var(--text-tertiary)',
        accent: 'var(--accent)',
        healthy: 'var(--health-good)',
        watch: 'var(--health-watch)',
        warning: 'var(--health-warn)',
        critical: 'var(--health-crit)',
        anomaly: 'var(--anomaly)',
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', '-apple-system', 'sans-serif'],
        mono: ['JetBrains Mono', 'ui-monospace', 'SFMono-Regular', 'monospace'],
      },
      borderRadius: { sm: '6px', md: '10px', lg: '16px', xl: '24px' },
      transitionTimingFunction: { spring: 'cubic-bezier(0.16, 1, 0.3, 1)' },
    },
  },
  plugins: [],
} satisfies Config;
