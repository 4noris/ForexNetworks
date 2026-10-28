import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App.jsx";
import "./index.css";
import { applyStoredTheme } from "./lib/theme.js";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

applyStoredTheme();

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      retry: false,
      gcTime: 5 * 60 * 1000,
      structuralSharing: true,
    },
  },
});

ReactDOM.createRoot(document.getElementById("root")).render(
  <QueryClientProvider client={queryClient}>
    <App />
  </QueryClientProvider>,
);
