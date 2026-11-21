/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        odoo: {
          purple: "#714B67",
          purpleDark: "#5a3b52",
          teal: "#017E84",
          surface: "#F8F9FA",
        },
      },
    },
  },
  plugins: [],
};
