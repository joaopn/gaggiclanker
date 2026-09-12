import { screen, waitFor } from "@testing-library/react";
import { useForm } from "react-hook-form";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ResolvedSetting } from "@/api/types";
import { LlmSection } from "@/pages/settings/LlmSection";
import type { SettingsFormValues } from "@/pages/settings/schema";
import { renderWithQueryClient, setupUser } from "@/test/renderWithQueryClient";

const { toastSuccess, toastError } = vi.hoisted(() => ({
  toastSuccess: vi.fn(),
  toastError: vi.fn(),
}));
vi.mock("sonner", () => ({
  toast: { success: toastSuccess, error: toastError, info: vi.fn() },
  Toaster: () => null,
}));

const { getLlmStatus, validateLlm, getLlmModels, resetLlmRateLimit } = vi.hoisted(() => ({
  getLlmStatus: vi.fn(),
  validateLlm: vi.fn(),
  getLlmModels: vi.fn(),
  resetLlmRateLimit: vi.fn(),
}));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  getLlmStatus,
  validateLlm,
  getLlmModels,
  resetLlmRateLimit,
}));

function plain(key: string, value: string): ResolvedSetting {
  return {
    key,
    type: "string",
    secret: false,
    readonly: false,
    value,
    default: "",
    override: value,
    source: "database",
    description: `about ${key}`,
  };
}

function secret(key: string): ResolvedSetting {
  return {
    key,
    type: "string",
    secret: true,
    readonly: false,
    configured: true,
    hint: "sk-p",
    source: "database",
    description: `about ${key}`,
  };
}

const ENTRIES: ResolvedSetting[] = [
  plain("llmProvider", "claude_code"),
  plain("llmBaseUrl", ""),
  secret("llmApiKey"),
  secret("anthropicApiKey"),
  secret("claudeCodeOauthToken"),
  plain("claudeCodeBin", "claude"),
  plain("claudeCodeEffort", ""),
  plain("modelDefault", ""),
  plain("modelAnalysis", ""),
];

function Harness({ provider = "claude_code" }: { provider?: string }) {
  const form = useForm<SettingsFormValues>({
    defaultValues: {
      llmProvider: provider,
      llmBaseUrl: "",
      llmApiKey: "",
      anthropicApiKey: "",
      claudeCodeOauthToken: "",
      claudeCodeBin: "claude",
      claudeCodeEffort: "",
      modelDefault: "",
      modelAnalysis: "",
    },
  });
  return <LlmSection entries={ENTRIES} control={form.control} errors={form.formState.errors} />;
}

describe("LlmSection", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getLlmStatus.mockResolvedValue({
      provider: "claude_code",
      providers: ["openrouter", "ollama", "anthropic", "claude_code"],
      base_url: "",
      models: {},
      effort_levels: ["low", "medium", "high"],
      timeout_s: 300,
      rate_limit: { stopped: false, retries: 2, remaining: 2 },
      claude_code: { version: "2.1.268", authenticated: true, detail: "user@example.test (max)" },
    });
  });

  it("shows only the credential the chosen provider actually uses", async () => {
    renderWithQueryClient(<Harness provider="claude_code" />);

    expect(await screen.findByLabelText("Claude code oauth token")).toBeInTheDocument();
    // Another provider's key must not be offered here: the backend never lends
    // one provider's credential to another, and the form should not imply it.
    expect(screen.queryByLabelText("Llm api key")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Anthropic api key")).not.toBeInTheDocument();
  });

  it("hides the base URL for a provider whose endpoint may not be redirected", async () => {
    renderWithQueryClient(<Harness provider="openrouter" />);

    expect(await screen.findByLabelText("Llm api key")).toBeInTheDocument();
    expect(screen.queryByLabelText("Llm base url")).not.toBeInTheDocument();
  });

  it("offers the base URL for a self-hosted provider", async () => {
    renderWithQueryClient(<Harness provider="ollama" />);

    expect(await screen.findByLabelText("Llm base url")).toBeInTheDocument();
  });

  it("shows the Claude Code panel only when the CLI is the provider", async () => {
    const { unmount } = renderWithQueryClient(<Harness provider="claude_code" />);

    // The version arrives with the status query, so it is what proves the panel
    // rendered from real data rather than from its placeholders.
    expect(await screen.findByText("2.1.268")).toBeInTheDocument();
    expect(screen.getByTestId("claude-code-panel")).toBeInTheDocument();
    expect(screen.getByText("authenticated")).toBeInTheDocument();
    unmount();

    renderWithQueryClient(<Harness provider="anthropic" />);
    await screen.findByLabelText("Anthropic api key");
    expect(screen.queryByTestId("claude-code-panel")).not.toBeInTheDocument();
  });

  it("validates the stored credentials on demand, never on render", async () => {
    validateLlm.mockResolvedValue({ provider: "claude_code", ok: true, detail: "all good" });
    const user = setupUser();
    renderWithQueryClient(<Harness />);

    await screen.findByRole("button", { name: /validate credentials/i });
    expect(validateLlm).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: /validate credentials/i }));

    await waitFor(() => expect(toastSuccess).toHaveBeenCalledWith("all good"));
  });

  it("reports a refused credential as an error rather than a success", async () => {
    validateLlm.mockResolvedValue({ provider: "claude_code", ok: false, detail: "not logged in" });
    const user = setupUser();
    renderWithQueryClient(<Harness />);

    await user.click(await screen.findByRole("button", { name: /validate credentials/i }));

    await waitFor(() => expect(toastError).toHaveBeenCalledWith("not logged in"));
  });

  it("lists models only when asked, because listing is a request to the provider", async () => {
    getLlmModels.mockResolvedValue({ provider: "claude_code", models: ["sonnet", "haiku"] });
    const user = setupUser();
    renderWithQueryClient(<Harness />);

    await screen.findByRole("button", { name: /list models/i });
    expect(getLlmModels).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: /list models/i }));

    expect(await screen.findByTestId("model-suggestions")).toHaveTextContent("sonnet");
  });

  it("shows the rate-limit latch and clears it", async () => {
    getLlmStatus.mockResolvedValue({
      provider: "claude_code",
      providers: ["claude_code"],
      base_url: "",
      models: {},
      effort_levels: [],
      timeout_s: 300,
      rate_limit: { stopped: true, retries: 2, remaining: 0 },
      claude_code: {},
    });
    resetLlmRateLimit.mockResolvedValue({ stopped: false, retries: 2, remaining: 2 });
    const user = setupUser();
    renderWithQueryClient(<Harness />);

    await waitFor(() =>
      expect(screen.getByTestId("rate-limit-state")).toHaveTextContent("stopped"),
    );

    await user.click(screen.getByRole("button", { name: /clear the rate-limit stop/i }));

    await waitFor(() => expect(resetLlmRateLimit).toHaveBeenCalled());
  });
});
