import { screen, waitFor } from "@testing-library/react";
import { useForm } from "react-hook-form";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ResolvedSetting } from "@/api/types";
import { LlmModelsGroup, LlmProviderGroup, RateLimitLatch } from "@/pages/settings/LlmGroups";
import { groupFor, type SettingsFormValues } from "@/pages/settings/schema";
import { renderWithQueryClient, setupUser } from "@/test/renderWithQueryClient";

const { toastSuccess, toastError } = vi.hoisted(() => ({
  toastSuccess: vi.fn(),
  toastError: vi.fn(),
}));
vi.mock("sonner", () => ({
  toast: { success: toastSuccess, error: toastError, info: vi.fn() },
  Toaster: () => null,
}));

const {
  getLlmStatus,
  validateLlm,
  getLlmModels,
  resetLlmRateLimit,
  getClaudeCli,
  installClaudeCli,
  removeClaudeCli,
} = vi.hoisted(() => ({
  getLlmStatus: vi.fn(),
  validateLlm: vi.fn(),
  getLlmModels: vi.fn(),
  resetLlmRateLimit: vi.fn(),
  getClaudeCli: vi.fn(),
  installClaudeCli: vi.fn(),
  removeClaudeCli: vi.fn(),
}));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  getLlmStatus,
  validateLlm,
  getLlmModels,
  resetLlmRateLimit,
  getClaudeCli,
  installClaudeCli,
  removeClaudeCli,
}));

type Job = { state: string; target: string; version: string; message: string };

function cliStatus(overrides: {
  managed?: string | null;
  job?: Partial<Job>;
  overridden?: boolean;
}) {
  const managed = overrides.managed ?? null;
  return {
    platform_package: "@anthropic-ai/claude-code-linux-x64",
    bundled: { path: "/usr/local/bin/claude", version: "2.1.267" },
    managed: { path: managed ? `/app/data/claude-code/${managed}/claude` : null, version: managed },
    overridden: overrides.overridden ?? false,
    active_binary: managed ? `/app/data/claude-code/${managed}/claude` : "claude",
    channels: { stable: "2.1.273", latest: "2.1.281" },
    job: {
      state: "idle",
      target: "",
      version: "",
      message: "",
      started_at: null,
      finished_at: null,
      ...overrides.job,
    },
  };
}

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
  const props = { control: form.control, errors: form.formState.errors };
  // The three pieces the LLM page places in its Provider, Models and Limits
  // cards, together: the provider picked in one decides what the others show.
  return (
    <>
      <LlmProviderGroup
        entries={ENTRIES.filter((entry) => groupFor(entry.key) === "provider")}
        {...props}
      />
      <LlmModelsGroup
        entries={ENTRIES.filter((entry) => groupFor(entry.key) === "models")}
        {...props}
      />
      <RateLimitLatch />
    </>
  );
}

describe("LLM groups", () => {
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
    getClaudeCli.mockResolvedValue(cliStatus({}));
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

  it("validates on demand, never on render", async () => {
    validateLlm.mockResolvedValue({ provider: "claude_code", ok: true, detail: "all good" });
    const user = setupUser();
    renderWithQueryClient(<Harness />);

    await screen.findByRole("button", { name: /validate credentials/i });
    expect(validateLlm).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: /validate credentials/i }));

    await waitFor(() => expect(toastSuccess).toHaveBeenCalledWith("all good"));
    // Nothing typed: an empty draft, so the server tests what is stored.
    expect(validateLlm).toHaveBeenCalledWith({ settings: {} });
  });

  it("validates the token as typed, without a save first", async () => {
    validateLlm.mockResolvedValue({ provider: "claude_code", ok: true, detail: "all good" });
    const user = setupUser();
    renderWithQueryClient(<Harness />);

    await user.type(await screen.findByLabelText("Claude code oauth token"), "sk-ant-oat01-typed");
    await user.click(screen.getByRole("button", { name: /validate credentials/i }));

    await waitFor(() =>
      expect(validateLlm).toHaveBeenCalledWith({
        settings: { claudeCodeOauthToken: "sk-ant-oat01-typed" },
      }),
    );
  });

  it("validates the provider picked in the form, not the saved one", async () => {
    validateLlm.mockResolvedValue({ provider: "openrouter", ok: false, detail: "no key" });
    const user = setupUser();
    // Saved as claude_code (see ENTRIES); the form has moved to OpenRouter.
    renderWithQueryClient(<Harness provider="openrouter" />);

    await user.click(await screen.findByRole("button", { name: /validate credentials/i }));

    await waitFor(() =>
      expect(validateLlm).toHaveBeenCalledWith({ settings: { llmProvider: "openrouter" } }),
    );
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

  it("installs a Claude Code channel and follows the install to its end", async () => {
    installClaudeCli.mockResolvedValue(
      cliStatus({ job: { state: "running", target: "latest", version: "2.1.281" } }),
    );
    const user = setupUser();
    renderWithQueryClient(<Harness />);

    const button = await screen.findByRole("button", { name: /install latest \(2\.1\.281\)/i });
    getClaudeCli.mockResolvedValue(
      cliStatus({
        managed: "2.1.281",
        job: {
          state: "done",
          target: "latest",
          version: "2.1.281",
          message: "Claude Code 2.1.281 installed",
        },
      }),
    );
    await user.click(button);

    expect(installClaudeCli).toHaveBeenCalledWith("latest");
    await waitFor(
      () => expect(screen.getByTestId("claude-cli-managed")).toHaveTextContent("2.1.281"),
      { timeout: 3000 },
    );
    await waitFor(() => expect(toastSuccess).toHaveBeenCalledWith("Claude Code 2.1.281 installed"));
  });

  it("installs an exact version only once it looks like one", async () => {
    installClaudeCli.mockResolvedValue(cliStatus({}));
    const user = setupUser();
    renderWithQueryClient(<Harness />);

    const field = await screen.findByLabelText(/exact claude code version/i);
    const button = screen.getByRole("button", { name: /install version/i });
    await user.type(field, "2.1");
    expect(button).toBeDisabled();
    await user.type(field, ".250");
    expect(button).toBeEnabled();
    await user.click(button);

    expect(installClaudeCli).toHaveBeenCalledWith("2.1.250");
  });

  it("goes back to the image's binary on request", async () => {
    getClaudeCli.mockResolvedValue(cliStatus({ managed: "2.1.281" }));
    removeClaudeCli.mockResolvedValue(cliStatus({}));
    const user = setupUser();
    renderWithQueryClient(<Harness />);

    await user.click(await screen.findByRole("button", { name: /use the image's version/i }));

    await waitFor(() => expect(removeClaudeCli).toHaveBeenCalled());
  });

  it("says a custom binary setting wins over an installed release", async () => {
    getClaudeCli.mockResolvedValue(cliStatus({ overridden: true }));
    renderWithQueryClient(<Harness />);

    expect(await screen.findByTestId("claude-cli-overridden")).toBeInTheDocument();
  });

  it("shows why an install failed", async () => {
    getClaudeCli.mockResolvedValue(
      cliStatus({ job: { state: "failed", target: "latest", message: "npm did not answer" } }),
    );
    renderWithQueryClient(<Harness />);

    expect(await screen.findByTestId("claude-cli-job")).toHaveTextContent("npm did not answer");
  });
});
