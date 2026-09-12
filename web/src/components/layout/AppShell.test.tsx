import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "@/App";
import { NAV_LINKS } from "@/lib/navigation";
import { createTestQueryClient, setupUser } from "@/test/renderWithQueryClient";

const { getHealth, getSettings } = vi.hoisted(() => ({
  getHealth: vi.fn(),
  getSettings: vi.fn(),
}));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  getHealth,
  getSettings,
}));

function renderApp(path = "/shots") {
  return render(
    <QueryClientProvider client={createTestQueryClient()}>
      <MemoryRouter initialEntries={[path]}>
        <App />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("AppShell", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getHealth.mockResolvedValue({ status: "ok", version: "0.1.0", database: "ok" });
    getSettings.mockResolvedValue({});
  });

  it("lists every nav entry", () => {
    renderApp();
    const nav = screen.getAllByRole("navigation", { name: "Main" })[0];
    for (const link of NAV_LINKS) {
      expect(nav).toHaveTextContent(link.label);
    }
  });

  it("marks the current route", () => {
    renderApp("/beans");
    const current = screen.getAllByRole("link", { current: "page" });
    expect(current.some((el) => el.textContent?.includes("Beans"))).toBe(true);
  });

  it("navigates on the `g s` chord", async () => {
    const user = setupUser();
    renderApp("/beans");
    expect(screen.getByRole("heading", { name: "Beans" })).toBeInTheDocument();

    await user.keyboard("gs");

    await waitFor(() => expect(screen.getByRole("heading", { name: "Shots" })).toBeInTheDocument());
  });

  it("navigates to Settings on `g ,`", async () => {
    const user = setupUser();
    renderApp("/shots");
    await user.keyboard("g,");
    await waitFor(() => expect(getSettings).toHaveBeenCalled());
    expect(screen.getByRole("heading", { name: "Settings" })).toBeInTheDocument();
  });

  it("ignores a chord typed into a text field", async () => {
    const user = setupUser();
    getSettings.mockResolvedValue({
      gaggimateHost: {
        key: "gaggimateHost",
        type: "string",
        secret: false,
        value: "",
        default: "",
        override: null,
        source: "default",
        description: "Hostname or IP of the display board.",
      },
    });
    renderApp("/settings");

    const host = await screen.findByLabelText("Gaggimate host");
    await user.click(host);
    await user.keyboard("gs");

    // Still on Settings, and the letters landed in the box: a shortcut that
    // fires mid-sentence is worse than no shortcut at all.
    expect(screen.getByRole("heading", { name: "Settings" })).toBeInTheDocument();
    expect(host).toHaveValue("gs");
  });

  it("opens the shortcut sheet on ?", async () => {
    const user = setupUser();
    renderApp();
    await user.keyboard("?");
    expect(await screen.findByText("Keyboard shortcuts")).toBeInTheDocument();
  });

  it("renders the device status pill from /health", async () => {
    renderApp();
    await waitFor(() =>
      expect(screen.getByTestId("device-status-pill")).toHaveAttribute("data-state", "good"),
    );
    expect(screen.getByTestId("device-status-pill")).toHaveTextContent("Online");
  });

  it("shows the pill as offline when the backend is unreachable", async () => {
    getHealth.mockRejectedValue(new Error("Failed to fetch"));
    renderApp();
    await waitFor(() =>
      expect(screen.getByTestId("device-status-pill")).toHaveAttribute("data-state", "bad"),
    );
  });

  it("redirects / to the shots page", async () => {
    renderApp("/");
    expect(await screen.findByRole("heading", { name: "Shots" })).toBeInTheDocument();
  });

  it("shows a 404 page inside the shell for an unknown route", async () => {
    renderApp("/nope");
    expect(await screen.findByText("No such page")).toBeInTheDocument();
    // Still inside the shell, so the user can navigate out of it.
    expect(screen.getAllByRole("navigation", { name: "Main" }).length).toBeGreaterThan(0);
  });
});
