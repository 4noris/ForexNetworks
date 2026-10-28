/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        dark: {
          900: "rgb(var(--color-bg) / <alpha-value>)",
          800: "rgb(var(--color-surface) / <alpha-value>)",
          700: "rgb(var(--color-card) / <alpha-value>)",
        },
        // alias so old JSX classes still resolve
        accent: {
          400: "rgb(var(--color-accent-soft) / <alpha-value>)",
          500: "rgb(var(--color-accent) / <alpha-value>)",
          600: "rgb(var(--color-accent-strong) / <alpha-value>)",
          purple: "rgb(var(--color-accent-soft) / <alpha-value>)",
        },
        success: "#10b981",
        danger: "#ef4444",
        amber: "#f59e0b",
      },
      fontFamily: {
        sans: ["Segoe UI Variable", "Segoe UI", "system-ui", "sans-serif"],
        display: [
          "Segoe UI Variable Display",
          "Segoe UI",
          "system-ui",
          "sans-serif",
        ],
      },
      borderRadius: {
        "2xl": "1rem",
        "3xl": "1.25rem",
        "4xl": "1.75rem",
        "5xl": "2rem",
      },
      boxShadow: {
        surface: "var(--surface-shadow)",
        card: "var(--card-shadow)",
        ring: "var(--glow-ring)",
      },
      animation: {
        "fade-in": "fadeIn 0.35s ease both",
        "slide-up": "slideUp 0.4s cubic-bezier(.16,1,.3,1) both",
      },
      keyframes: {
        fadeIn: { from: { opacity: 0 }, to: { opacity: 1 } },
        slideUp: {
          from: { opacity: 0, transform: "translateY(12px)" },
          to: { opacity: 1, transform: "translateY(0)" },
        },
      },
    },
  },
  plugins: [],
};
