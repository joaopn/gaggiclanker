import { screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { SettingsMap } from "@/api/types";
import { SettingsPage } from "@/pages/settings/SettingsPage";
import { renderWithQueryClient, setupUser } from "@/test/renderWithQueryClient";

const { toastSuccess, toastError, toastInfo } = vi.hoisted(() => ({
  toastSuccess: vi.fn(),
  toastError: vi.fn(),
  toastInfo: vi.fn(),
}));
vi.mock("sonner", () => ({
  toast: { success: toastSuccess, error: toastError, info: toastInfo },
  Toaster: () => null,
}));

const { getSettings, patchSettings, getHealth, createBackup, getLlmStatus, getPrompts } =
  vi.hoisted(() => ({
    getSettings: vi.fn(),
    patchSettings: vi.fn(),
    getHealth: vi.fn(),
    createBackup: vi.fn(),
    getLlmStatus: vi.fn(),
    getPrompts: vi.fn(),
  }));
// Partial: the page pulls ApiClientError in through useQueryErrorToast, and a
// factory that enumerates exports would have to be edited every time the client
// grows one.
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  getSettings,
  patchSettings,
  getHealth,
  createBackup,
  getLlmStatus,
  getPrompts,
}));

/** Shaped exactly like `ResolvedSetting.to_api()` in gaggiclanker/settings.py. */
function settingsFixture(): SettingsMap {
  return {
    gaggimateHost: {
      key: "gaggimateHost",
      type: "string",
      secret: false,
      value: "10.0.0.5",
      default: "",
      override: "10.0.0.5",
      source: "database",
      description: "Hostname or IP of the GaggiMate display board.",
    },
    deviceSyncEnabled: {
      key: "deviceSyncEnabled",
      type: "bool",
      secret: false,
      value: true,
      default: true,
      override: null,
      source: "default",
      description: "Hold the WebSocket and mirror shots.",
    },
    devicePollIntervalSeconds: {
      key: "devicePollIntervalSeconds",
      type: "int",
      secret: false,
      value: 60,
      default: 60,
      override: null,
      source: "default",
      description: "How often to re-diff the shot index.",
    },
    llmProvider: {
      key: "llmProvider",
      type: "string",
      secret: false,
      value: "openrouter",
      default: "claude_code",
      override: "openrouter",
      source: "database",
      description: "Which provider answers a call.",
    },
    llmApiKey: {
      key: "llmApiKey",
      type: "string",
      secret: true,
      configured: true,
      hint: "sk-p",
      source: "environment",
      description: "API key for the configured LLM provider.",
    },
  };
}

describe("SettingsPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getSettings.mockResolvedValue(settingsFixture());
    getHealth.mockResolvedValue({ status: "ok", version: "0.1.0", database: "ok" });
    // The LLM section and the prompt editor both read on mount. Neither is
    // what these tests are about, but an unmocked fetch in jsdom is an
    // unhandled rejection rather than a quiet failure.
    getLlmStatus.mockResolvedValue({
      provider: "openrouter",
      providers: ["openrouter", "claude_code"],
      base_url: "",
      models: {},
      effort_levels: ["low", "high"],
      timeout_s: 300,
      rate_limit: { stopped: false, retries: 2, remaining: 2 },
      claude_code: {},
    });
    getPrompts.mockResolvedValue({ prompts: [] });
    patchSettings.mockImplementation(async () => settingsFixture());
  });

  it("renders a control per registry entry, chosen by the declared type", async () => {
    renderWithQueryClient(<SettingsPage />);

    const host = await screen.findByLabelText("Gaggimate host");
    expect(host).toHaveValue("10.0.0.5");
    expect(screen.getByLabelText("Device poll interval seconds")).toHaveValue("60");
    // A bool gets a select, not a text box.
    expect(screen.getByLabelText("Device sync enabled")).toHaveAttribute("role", "combobox");
  });

  it("shows a secret as a hint and never as a value", async () => {
    renderWithQueryClient(<SettingsPage />);

    const field = (await screen.findByLabelText("Llm api key")) as HTMLInputElement;
    expect(field).toHaveValue("");
    expect(field).toHaveAttribute("type", "password");
    expect(field).toHaveAttribute("placeholder", "leave blank to keep");
    expect(screen.getByText("set - sk-p...")).toBeInTheDocument();
  });

  it("labels where each value came from, because precedence is the thing people get wrong", async () => {
    renderWithQueryClient(<SettingsPage />);
    await screen.findByLabelText("Gaggimate host");
    expect(screen.getAllByText("saved here").length).toBeGreaterThan(0);
    expect(screen.getAllByText("default").length).toBeGreaterThan(0);
    expect(screen.getByText("from the environment")).toBeInTheDocument();
  });

  it("PATCHes only the fields that changed, and toasts on success", async () => {
    const user = setupUser();
    renderWithQueryClient(<SettingsPage />);

    const host = await screen.findByLabelText("Gaggimate host");
    await user.clear(host);
    await user.type(host, "10.0.0.9");
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() => expect(patchSettings).toHaveBeenCalledTimes(1));
    // Not the whole form: writing every key back would turn an environment
    // baseline into a database override on the first save.
    // `mock.calls[0][0]` rather than toHaveBeenCalledWith: react-query 5 passes
    // a mutation context as a second argument to every mutationFn.
    expect(patchSettings.mock.calls[0][0]).toEqual({ gaggimateHost: "10.0.0.9" });
    await waitFor(() => expect(toastSuccess).toHaveBeenCalledWith("Settings saved"));
  });

  it("leaves a blank secret alone", async () => {
    const user = setupUser();
    renderWithQueryClient(<SettingsPage />);

    const host = await screen.findByLabelText("Gaggimate host");
    await user.clear(host);
    await user.type(host, "10.0.0.9");
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() => expect(patchSettings).toHaveBeenCalled());
    expect(patchSettings.mock.calls[0][0]).not.toHaveProperty("llmApiKey");
  });

  it("sends a typed secret through", async () => {
    const user = setupUser();
    renderWithQueryClient(<SettingsPage />);

    await user.type(await screen.findByLabelText("Llm api key"), "sk-new-key");
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() => expect(patchSettings).toHaveBeenCalled());
    expect(patchSettings.mock.calls[0][0]).toEqual({ llmApiKey: "sk-new-key" });
  });

  it("refuses to save a non-integer in an int field", async () => {
    const user = setupUser();
    renderWithQueryClient(<SettingsPage />);

    const poll = await screen.findByLabelText("Device poll interval seconds");
    await user.clear(poll);
    await user.type(poll, "sixty");
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    expect(await screen.findByText("expected an integer")).toBeInTheDocument();
    expect(patchSettings).not.toHaveBeenCalled();
  });

  it("says so instead of sending an empty PATCH", async () => {
    const user = setupUser();
    renderWithQueryClient(<SettingsPage />);
    await screen.findByLabelText("Gaggimate host");

    await user.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() => expect(toastInfo).toHaveBeenCalledWith("Nothing to save"));
    expect(patchSettings).not.toHaveBeenCalled();
  });

  it("surfaces a save failure without losing the edit", async () => {
    const user = setupUser();
    patchSettings.mockRejectedValueOnce(new Error("Database is unavailable"));
    renderWithQueryClient(<SettingsPage />);

    const host = await screen.findByLabelText("Gaggimate host");
    await user.clear(host);
    await user.type(host, "10.0.0.9");
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() => expect(toastError).toHaveBeenCalledWith("Database is unavailable"));
    expect(screen.getByLabelText("Gaggimate host")).toHaveValue("10.0.0.9");
  });

  it("shows an error state when the registry cannot be read", async () => {
    getSettings.mockRejectedValue(new Error("Expected JSON but received HTML"));
    renderWithQueryClient(<SettingsPage />);

    expect(await screen.findByText("Could not load settings")).toBeInTheDocument();
  });

  it("renders /health and backs the database up on demand", async () => {
    const user = setupUser();
    createBackup.mockResolvedValue({
      filename: "gaggiclanker-20260101.db",
      path: "/app/data/backups/gaggiclanker-20260101.db",
      size_bytes: 4096,
      created_at: "2026-01-01T00:00:00Z",
    });
    renderWithQueryClient(<SettingsPage />);

    await waitFor(() => expect(screen.getByTestId("health-version")).toHaveTextContent("0.1.0"));
    expect(screen.getByTestId("health-database")).toHaveTextContent("ok");

    await user.click(screen.getByRole("button", { name: "Back up database" }));
    await waitFor(() => expect(createBackup).toHaveBeenCalledTimes(1));
    await waitFor(() =>
      expect(toastSuccess).toHaveBeenCalledWith("Backup written: gaggiclanker-20260101.db"),
    );
  });
});
