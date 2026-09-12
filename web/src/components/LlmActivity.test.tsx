import { screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { LlmCall } from "@/api/types";
import { LlmActivity } from "@/components/LlmActivity";
import { renderWithQueryClient, setupUser } from "@/test/renderWithQueryClient";

const { getLlmCalls } = vi.hoisted(() => ({ getLlmCalls: vi.fn() }));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  getLlmCalls,
}));

// The stream is exercised in useSse's own tests; here it would only add a
// reconnect loop against a jsdom fetch that does not exist.
const { subscribeToEventSource } = vi.hoisted(() => ({
  subscribeToEventSource: vi.fn(() => () => {}),
}));
vi.mock("@/lib/sse", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/sse")>()),
  subscribeToEventSource,
}));

function call(overrides: Partial<LlmCall> = {}): LlmCall {
  return {
    id: "1",
    label: "analyse shot",
    subject: "#129",
    provider: "claude_code",
    model: "sonnet",
    purpose: "analysis",
    status: "succeeded",
    started_at: "2026-09-11T00:00:00.000Z",
    completed_at: "2026-09-11T00:00:42.000Z",
    duration_ms: 42000,
    prompt_tokens: 1200,
    completion_tokens: 300,
    total_tokens: 1500,
    mode: "json_schema",
    error: null,
    ...overrides,
  };
}

describe("LlmActivity", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getLlmCalls.mockResolvedValue({ calls: [], running: 0 });
  });

  it("shows no count when nothing is running", async () => {
    renderWithQueryClient(<LlmActivity />);

    const button = await screen.findByTestId("llm-activity");
    expect(button).toHaveTextContent("");
    expect(button).toHaveAccessibleName("LLM activity");
  });

  it("counts the calls in flight", async () => {
    getLlmCalls.mockResolvedValue({
      calls: [call({ id: "a", status: "running", duration_ms: null, total_tokens: null })],
      running: 1,
    });
    renderWithQueryClient(<LlmActivity />);

    await waitFor(() =>
      expect(screen.getByTestId("llm-activity")).toHaveAccessibleName("1 LLM calls running"),
    );
  });

  it("lists recent calls with what they cost", async () => {
    getLlmCalls.mockResolvedValue({ calls: [call()], running: 0 });
    const user = setupUser();
    renderWithQueryClient(<LlmActivity />);

    await waitFor(() => expect(getLlmCalls).toHaveBeenCalled());
    await user.click(screen.getByTestId("llm-activity"));

    expect(await screen.findByText("analyse shot")).toBeInTheDocument();
    expect(screen.getByText("#129")).toBeInTheDocument();
    expect(screen.getByText(/claude_code - sonnet - 1500 tokens/)).toBeInTheDocument();
    expect(screen.getByText("42.0s")).toBeInTheDocument();
  });

  it("does not render an unreported token count as zero", async () => {
    // A provider that said nothing must not be made to look free.
    getLlmCalls.mockResolvedValue({ calls: [call({ total_tokens: null })], running: 0 });
    const user = setupUser();
    renderWithQueryClient(<LlmActivity />);

    await waitFor(() => expect(getLlmCalls).toHaveBeenCalled());
    await user.click(screen.getByTestId("llm-activity"));

    expect(await screen.findByText("claude_code - sonnet")).toBeInTheDocument();
    expect(screen.queryByText(/0 tokens/)).not.toBeInTheDocument();
  });

  it("shows why a call failed", async () => {
    getLlmCalls.mockResolvedValue({
      calls: [call({ status: "failed", error: "invalid api key" })],
      running: 0,
    });
    const user = setupUser();
    renderWithQueryClient(<LlmActivity />);

    await waitFor(() => expect(getLlmCalls).toHaveBeenCalled());
    await user.click(screen.getByTestId("llm-activity"));

    expect(await screen.findByText("invalid api key")).toBeInTheDocument();
  });
});
