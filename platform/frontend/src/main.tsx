import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter } from "react-router-dom";

import { App } from "./App";
import { WorkingContextProvider } from "./context/WorkingContext";
import "./styles/global.css";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 15_000,
      refetchOnWindowFocus: false,
      // A 401 or 403 is an answer, not a transient fault, so retrying it only
      // delays the sign-in screen the user actually needs.
      retry: (failureCount, error) => {
        const status = (error as { status?: number }).status;
        if (status && status < 500) return false;
        return failureCount < 2;
      },
    },
  },
});

const container = document.getElementById("root");
if (!container) throw new Error("The root element is missing from index.html");

createRoot(container).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      {/* Opt in to the v7 behaviours now, so the upgrade is not a
          behavioural change later. */}
      <BrowserRouter
        future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
      >
        <WorkingContextProvider>
          <App />
        </WorkingContextProvider>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);
