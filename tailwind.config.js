/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ['./src/renderer/**/*.{html,js,jsx,ts,tsx}'],
  theme: {
    extend: {
      colors: {
        resolve: {
          bg: '#1a1a2e',
          surface: '#222244',
          surfaceLight: '#2a2a4a',
          border: '#333366',
          accent: '#6c63ff',
          accentHover: '#7b73ff',
          success: '#4ade80',
          warning: '#fbbf24',
          error: '#f87171',
          text: '#e2e8f0',
          textDim: '#94a3b8',
          textMuted: '#64748b'
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
