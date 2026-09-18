import { screen, waitFor } from "@testing-library/react";
import { Route, Routes } from "react-router-dom";
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
      readonly: false,
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
      readonly: false,
      value: true,
      default: true,
      override: null,
      source: "default",
      description: "Hold the WebSocket so a pull can reach the machine.",
    },
    deviceCleanupKeepNewest: {
      key: "deviceCleanupKeepNewest",
      type: "int",
      secret: false,
      readonly: false,
      value: 50,
      default: 50,
      override: null,
      source: "default",
      description: "How many shots to leave on the machine.",
    },
    llmProvider: {
      key: "llmProvider",
      type: "string",
      secret: false,
      readonly: false,
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
      readonly: false,
      configured: true,
      hint: "sk-p",
      source: "database",
      description: "API key for the configured LLM provider.",
    },
    chatMaxToolRounds: {
      key: "chatMaxToolRounds",
      type: "int",
      secret: false,
      readonly: false,
      value: 8,
      default: 8,
      override: null,
      source: "default",
      description: "How many provider round-trips one chat answer may take.",
    },
    llmTimeoutSeconds: {
      key: "llmTimeoutSeconds",
      type: "float",
      secret: false,
      readonly: false,
      value: 300,
      default: 300,
      override: null,
      source: "default",
      description: "How long one attempt may take.",
    },
  };
}

function renderAt(path: string) {
  return renderWithQueryClient(
    <Routes>
      <Route path="/settings/:page" element={<SettingsPage />} />
    </Routes>,
    { initialEntries: [path] },
  );
}

describe("SettingsPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getSettings.mockResolvedValue(settingsFixture());
    getHealth.mockResolvedValue({ status: "ok", version: "0.1.0", database: "ok" });
    // The LLM page reads the provider status on mount. It is not what most of
    // these tests are about, but an unmocked fetch in jsdom is an unhandled
    // rejection rather than a quiet failure.
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

  it("titles each page after its sidebar entry", async () => {
    renderAt("/settings/safety");
    expect(await screen.findByRole("heading", { name: "Profile safety" })).toBeInTheDocument();
  });

  it("puts the chat budgets on the LLM page, under their own heading", async () => {
    renderAt("/settings/llm#chat");
    expect(await screen.findByLabelText("Chat max tool rounds")).toBeVisible();
    expect(screen.getByRole("button", { name: "Chat" })).toHaveAttribute("aria-expanded", "true");
  });

  it("sends the retired General page to the LLM page, keeping the card it named", async () => {
    renderWithQueryClient(
      <Routes>
        <Route path="/settings/:page" element={<SettingsPage />} />
      </Routes>,
      { initialEntries: ["/settings/general#chat"] },
    );
    expect(await screen.findByRole("heading", { name: "LLM" })).toBeInTheDocument();
    expect(await screen.findByLabelText("Chat max tool rounds")).toBeVisible();
  });

  it("answers an unknown settings page with the not-found page", () => {
    renderAt("/settings/nothing-here");
    expect(screen.getByText("No such page")).toBeInTheDocument();
  });

  it("shows only the page's own settings, under their headings", async () => {
    renderAt("/settings/machine");
    await screen.findByLabelText("Gaggimate host");
    expect(screen.queryByLabelText("Llm api key")).not.toBeInTheDocument();
    // Headings with nothing in them are left out: the fixture has no writes keys.
    expect(screen.getByRole("button", { name: "Connection" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Storage cleanup" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Writes" })).not.toBeInTheDocument();
  });

  it("starts with every card closed, and opens one on a click", async () => {
    const user = setupUser();
    renderAt("/settings/machine");
    const host = await screen.findByLabelText("Gaggimate host");

    const connection = screen.getByRole("button", { name: "Connection" });
    const cleanup = screen.getByRole("button", { name: "Storage cleanup" });
    expect(connection).toHaveAttribute("aria-expanded", "false");
    expect(cleanup).toHaveAttribute("aria-expanded", "false");
    // Closed, not unmounted: the field keeps its value for the save.
    expect(host).not.toBeVisible();
    expect(host).toHaveValue("10.0.0.5");

    await user.click(connection);
    expect(connection).toHaveAttribute("aria-expanded", "true");
    expect(
      document.getElementById(String(connection.getAttribute("aria-controls"))),
    ).toContainElement(host);
    expect(host).toBeVisible();
    expect(cleanup).toHaveAttribute("aria-expanded", "false");

    await user.click(connection);
    expect(host).not.toBeVisible();
  });

  it("opens the card a link names", async () => {
    renderAt("/settings/machine#cleanup");
    expect(await screen.findByLabelText("Device cleanup keep newest")).toBeVisible();
    expect(screen.getByLabelText("Gaggimate host")).not.toBeVisible();
  });

  it("renders a control per registry entry, chosen by the declared type", async () => {
    renderAt("/settings/machine");

    const host = await screen.findByLabelText("Gaggimate host");
    expect(host).toHaveValue("10.0.0.5");
    expect(screen.getByLabelText("Device cleanup keep newest")).toHaveValue("50");
    // A bool gets a select, not a text box.
    expect(screen.getByLabelText("Device sync enabled")).toHaveAttribute("role", "combobox");
  });

  it("shows a secret as a hint and never as a value", async () => {
    renderAt("/settings/llm");

    const field = (await screen.findByLabelText("Llm api key")) as HTMLInputElement;
    expect(field).toHaveValue("");
    expect(field).toHaveAttribute("type", "password");
    expect(field).toHaveAttribute("placeholder", "leave blank to keep");
    expect(screen.getByText("set - sk-p...")).toBeInTheDocument();
  });

  it("labels where each value came from: saved here, or the shipped default", async () => {
    renderAt("/settings/machine");
    await screen.findByLabelText("Gaggimate host");
    expect(screen.getAllByText("saved here").length).toBeGreaterThan(0);
    expect(screen.getAllByText("default").length).toBeGreaterThan(0);
    expect(screen.queryByText("from the environment")).not.toBeInTheDocument();
  });

  it("PATCHes only the fields that changed, and toasts on success", async () => {
    const user = setupUser();
    renderAt("/settings/machine");

    await user.click(await screen.findByRole("button", { name: "Connection" }));
    const host = screen.getByLabelText("Gaggimate host");
    await user.clear(host);
    await user.type(host, "10.0.0.9");
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() => expect(patchSettings).toHaveBeenCalledTimes(1));
    // Not the whole form: writing every key back would freeze every shipped
    // default as a stored row on the first save.
    // `mock.calls[0][0]` rather than toHaveBeenCalledWith: react-query 5 passes
    // a mutation context as a second argument to every mutationFn.
    expect(patchSettings.mock.calls[0][0]).toEqual({ gaggimateHost: "10.0.0.9" });
    await waitFor(() => expect(toastSuccess).toHaveBeenCalledWith("Settings saved"));
  });

  it("leaves a blank secret alone", async () => {
    const user = setupUser();
    renderAt("/settings/llm");

    await user.click(await screen.findByRole("button", { name: "Limits and the call ledger" }));
    const timeout = screen.getByLabelText("Llm timeout seconds");
    await user.clear(timeout);
    await user.type(timeout, "120");
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() => expect(patchSettings).toHaveBeenCalled());
    expect(patchSettings.mock.calls[0][0]).toEqual({ llmTimeoutSeconds: 120 });
  });

  it("sends a typed secret through", async () => {
    const user = setupUser();
    renderAt("/settings/llm");

    await user.click(await screen.findByRole("button", { name: "Provider" }));
    await user.type(screen.getByLabelText("Llm api key"), "sk-new-key");
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() => expect(patchSettings).toHaveBeenCalled());
    expect(patchSettings.mock.calls[0][0]).toEqual({ llmApiKey: "sk-new-key" });
  });

  it("refuses to save a non-integer, and opens the closed card that holds it", async () => {
    const user = setupUser();
    renderAt("/settings/machine");
    const cleanup = await screen.findByRole("button", { name: "Storage cleanup" });

    await user.click(cleanup);
    const keep = screen.getByLabelText("Device cleanup keep newest");
    await user.clear(keep);
    await user.type(keep, "fifty");
    await user.click(cleanup);
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    expect(await screen.findByText("expected an integer")).toBeVisible();
    expect(cleanup).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("button", { name: "Connection" })).toHaveAttribute(
      "aria-expanded",
      "false",
    );
    expect(patchSettings).not.toHaveBeenCalled();
  });

  it("says so instead of sending an empty PATCH", async () => {
    const user = setupUser();
    renderAt("/settings/machine");
    await screen.findByLabelText("Gaggimate host");

    await user.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() => expect(toastInfo).toHaveBeenCalledWith("Nothing to save"));
    expect(patchSettings).not.toHaveBeenCalled();
  });

  it("surfaces a save failure without losing the edit", async () => {
    const user = setupUser();
    patchSettings.mockRejectedValueOnce(new Error("Database is unavailable"));
    renderAt("/settings/machine");

    await user.click(await screen.findByRole("button", { name: "Connection" }));
    const host = screen.getByLabelText("Gaggimate host");
    await user.clear(host);
    await user.type(host, "10.0.0.9");
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() => expect(toastError).toHaveBeenCalledWith("Database is unavailable"));
    expect(screen.getByLabelText("Gaggimate host")).toHaveValue("10.0.0.9");
  });

  it("shows an error state when the registry cannot be read", async () => {
    getSettings.mockRejectedValue(new Error("Expected JSON but received HTML"));
    renderAt("/settings/machine");

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
    renderAt("/settings/system");

    await user.click(screen.getByRole("button", { name: "Status" }));
    await waitFor(() => expect(screen.getByTestId("health-version")).toHaveTextContent("0.1.0"));
    expect(screen.getByTestId("health-database")).toBeVisible();
    expect(screen.getByTestId("health-database")).toHaveTextContent("ok");

    await user.click(screen.getByRole("button", { name: "Backup" }));
    await user.click(screen.getByRole("button", { name: "Back up database" }));
    await waitFor(() => expect(createBackup).toHaveBeenCalledTimes(1));
    await waitFor(() =>
      expect(toastSuccess).toHaveBeenCalledWith("Backup written: gaggiclanker-20260101.db"),
    );
  });

  it("opens the import page's only card", () => {
    renderAt("/settings/import");
    expect(screen.getByRole("link", { name: "Open the shots page" })).toBeVisible();
  });
});
