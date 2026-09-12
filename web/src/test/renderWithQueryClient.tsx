import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { type RenderOptions, render, renderHook } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactElement, ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";
import { TooltipProvider } from "@/components/ui/tooltip";

/**
 * `retry: false` matters: with the app's default of 1, a test asserting on an
 * error state waits for a retry that the fake timers never advance, and fails
 * as a timeout rather than as the assertion it is.
 */
export function createTestQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0, staleTime: 0 },
      mutations: { retry: false },
    },
  });
}

type Options = Omit<RenderOptions, "wrapper"> & {
  /** Routes the component under test may navigate between. */
  initialEntries?: string[];
  queryClient?: QueryClient;
};

export function renderWithQueryClient(ui: ReactElement, options: Options = {}) {
  const {
    initialEntries = ["/"],
    queryClient = createTestQueryClient(),
    ...renderOptions
  } = options;

  const Wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={initialEntries}>
        <TooltipProvider>{children}</TooltipProvider>
      </MemoryRouter>
    </QueryClientProvider>
  );

  return { queryClient, ...render(ui, { wrapper: Wrapper, ...renderOptions }) };
}

export function renderHookWithQueryClient<TResult>(callback: () => TResult) {
  const queryClient = createTestQueryClient();
  const Wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
  return { queryClient, ...renderHook(callback, { wrapper: Wrapper }) };
}

/**
 * `userEvent.setup()` with `delay: null`.
 *
 * The default inserts a real `setTimeout(0)` between every synthetic event.
 * Combined with radix's own pointer timers (a Tooltip trigger is the usual
 * victim) that intermittently wedges a click for the full 5 s test timeout —
 * a flake that looks like a component bug and is not. Dispatching
 * synchronously removes the window entirely.
 */
export function setupUser() {
  return userEvent.setup({ delay: null });
}
