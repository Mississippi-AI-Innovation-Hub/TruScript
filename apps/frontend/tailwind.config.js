/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        brand: {
          50:  '#eef6ff',
          100: '#daeeff',
          200: '#bde1fe',
          300: '#7bc2fa',
          400: '#5aaef6',
          500: '#628ff2',
          600: '#4a72e8',
          700: '#3a5bd4',
          800: '#2e48ac',
          900: '#1e2f6e',
        },
      },
      backgroundImage: {
        'brand-gradient': 'linear-gradient(0deg, #7bc2fa 0%, #628ff2 100%)',
        'brand-gradient-dark': 'linear-gradient(0deg, #5aaef6 0%, #4a72e8 100%)',
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
      },
    },
  },
  plugins: [],
}
