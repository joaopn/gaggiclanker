import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "@/App";
import { NAV_LINKS } from "@/lib/navigation";
import { createTestQueryClient, setupUser } from "@/test/renderWithQueryClient";

const { getHealth, getSettings, getDeviceStatus } = vi.hoisted(() => ({
  getHealth: vi.fn(),
  getSettings: vi.fn(),
  getDeviceStatus: vi.fn(),
}));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  getHealth,
  getSettings,
  getDeviceStatus,
}));
// The app subscribes to /api/sync/events. There is no server behind jsdom, so
// leave the stream inert rather than letting every test start a reconnect loop
// against a fetch that will never succeed.
vi.mock("@/lib/sse", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/sse")>()),
  subscribeToEventSource: () => () => {},
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
    getDeviceStatus.mockResolvedValue({ configured: false, connected: false });
  });

  it("lists every nav entry", () => {
    renderApp();
    const nav = screen.getAllByRole("navigation", { name: "Main" })[0];
    for (const link of NAV_LINKS) {
      expect(nav).toHaveTextContent(link.label);
    }
  });

  it("lists the eight destinations in order, and nothing else", () => {
    renderApp();
    const nav = screen.getAllByRole("navigation", { name: "Main" })[0];
    // The label span, not the anchor: the anchor's text carries the chord too.
    const labels = Array.from(nav.querySelectorAll("a")).map((link) =>
      link.querySelector("span")?.textContent?.trim(),
    );
    expect(labels).toEqual([
      "Shots",
      "Chat",
      "Profiles",
      "Sets",
      "Beans",
      "Hardware",
      "Knowledge",
      "Settings",
    ]);
  });

  // The three retired chords. `g i`, `g d` and `g r` used to be Import, Device
  // and Drafts; the pages they led to are a drop zone, a pill and a section
  // now, so the letters must be free rather than quietly landing somewhere.
  it.each(["gi", "gd", "gr"])("does nothing on the retired chord %s", async (chord) => {
    const user = setupUser();
    renderApp("/beans");
    await user.keyboard(chord);
    expect(screen.getByRole("heading", { name: "Beans" })).toBeInTheDocument();
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

  it("renders the device status pill from /health with no machine configured", async () => {
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

  it("redirects the retired import route to the shots page", async () => {
    // An old bookmark, or a hard refresh on one: the drop zone that replaced
    // that page is on the shots page, so that is where it lands.
    renderApp("/import");
    expect(await screen.findByRole("heading", { name: "Shots" })).toBeInTheDocument();
  });

  it("shows a 404 page inside the shell for an unknown route", async () => {
    renderApp("/nope");
    expect(await screen.findByText("No such page")).toBeInTheDocument();
    // Still inside the shell, so the user can navigate out of it.
    expect(screen.getAllByRole("navigation", { name: "Main" }).length).toBeGreaterThan(0);
  });
});
