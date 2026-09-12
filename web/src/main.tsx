import { App } from "@/App";
import "@/index.css";
import { QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { queryClient } from "@/lib/queryClient";
import { applyThemeClass } from "@/lib/theme";

// The pre-paint script in index.html has already stamped the palette; this
// re-stamps from the same store so the two can never disagree after hydration.
applyThemeClass();

const container = document.getElementById("root");
if (!container) throw new Error("#root is missing from index.html");

createRoot(container).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);
