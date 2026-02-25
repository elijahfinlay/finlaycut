/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ['./src/renderer/**/*.{html,js,jsx,ts,tsx}'],
  theme: {
    extend: {
      colors: {
        resolve: {
          bg: '#141418',
          surface: '#1c1c22',
          surfaceLight: '#26262e',
          border: '#333340',
          accent: '#3b82f6',
          accentHover: '#60a5fa',
          success: '#4ade80',
          warning: '#fbbf24',
          error: '#f87171',
          text: '#e4e4e8',
          textDim: '#9ca3af',
          textMuted: '#6b7280'
        }
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', '-apple-system', 'sans-serif'],
        mono: ['JetBrains Mono', 'Menlo', 'monospace']
      }
    }
  },
  plugins: []
}
