/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{vue,js}'],
  theme: {
    extend: {
      fontFamily: {
        display: ['Chakra Petch', 'sans-serif'],
        body: ['Outfit', 'sans-serif'],
        mono: ['Space Mono', 'monospace'],
      },
      colors: {
        zinc: { 500: '#85858F', 600: '#8B8B95', 700: '#63636B', 850: '#1c1c20', 925: '#111113' },
        lime: { accent: '#BFFF00' },
        coral: { accent: '#FF3B5C' },
      },
    },
  },
  plugins: [],
}
