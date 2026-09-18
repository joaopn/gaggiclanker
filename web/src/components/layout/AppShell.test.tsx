import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "@/App";
import { isNavGroup, NAV_LINKS } from "@/lib/navigation";
import { SETTINGS_PAGES } from "@/lib/settingsPages";
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
    window.localStorage.clear();
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

  it("lists the five rows in order, and nothing else", () => {
    renderApp();
    const nav = screen.getAllByRole("navigation", { name: "Main" })[0];
    // The label span, not the row: the row's text carries the chord too. The
    // top-level rows only — a group is a disclosure, and its pages are a list
    // of their own under it.
    const rows = Array.from(nav.querySelectorAll(":scope > a, :scope > div > button"));
    const labels = rows.map((row) => row.querySelector("span")?.textContent?.trim());
    expect(labels).toEqual(["Shots", "Chat", "Brew setup", "Machine", "Settings"]);
  });

  describe("the groups", () => {
    function groupList(name: string) {
      const button = within(screen.getByTestId("sidebar")).getByRole("button", { name });
      const list = document.getElementById(String(button.getAttribute("aria-controls")));
      return { button, list: list as HTMLElement };
    }

    it("puts the brew setup and the machine's pages under their groups", async () => {
      const user = setupUser();
      renderApp("/shots");

      const brew = groupList("Brew setup");
      const machine = groupList("Machine");
      expect(brew.button).toHaveAttribute("aria-expanded", "false");
      expect(machine.button).toHaveAttribute("aria-expanded", "false");

      await user.click(brew.button);
      await user.click(machine.button);

      const hrefs = (list: HTMLElement) =>
        within(list)
          .getAllByRole("link")
          .map((link) => link.getAttribute("href"));
      expect(hrefs(brew.list)).toEqual(["/sets", "/beans", "/hardware"]);
      // The device page has a row now, beside the other pages about the machine.
      expect(hrefs(machine.list)).toEqual(["/profiles", "/sync", "/device"]);
    });

    it("opens the group holding the current page, and only that one", () => {
      renderApp("/hardware");
      expect(groupList("Brew setup").button).toHaveAttribute("aria-expanded", "true");
      expect(groupList("Machine").button).toHaveAttribute("aria-expanded", "false");
      const current = within(screen.getByTestId("sidebar")).getAllByRole("link", {
        current: "page",
      });
      expect(current.map((link) => link.textContent)).toEqual(["Hardwareg h"]);
    });

    it("keeps each grouped page's chord", async () => {
      const user = setupUser();
      renderApp("/shots");
      await user.keyboard("ge");
      expect(await screen.findByRole("heading", { name: "Sets" })).toBeInTheDocument();
      await user.keyboard("gh");
      expect(await screen.findByRole("heading", { name: "Hardware" })).toBeInTheDocument();
    });

    it("remembers a group opened by hand, and forgets it when closed", async () => {
      const user = setupUser();
      const { unmount } = renderApp("/shots");
      await user.click(groupList("Machine").button);
      expect(JSON.parse(window.localStorage.getItem("sidebar.groups.v1") ?? "[]")).toEqual([
        "machine",
      ]);
      unmount();

      renderApp("/shots");
      const machine = groupList("Machine").button;
      expect(machine).toHaveAttribute("aria-expanded", "true");
      expect(groupList("Brew setup").button).toHaveAttribute("aria-expanded", "false");

      await user.click(machine);
      expect(JSON.parse(window.localStorage.getItem("sidebar.groups.v1") ?? "[]")).toEqual([]);
    });

    it("does not record a group that opened because its page is showing", () => {
      renderApp("/beans");
      expect(window.localStorage.getItem("sidebar.groups.v1")).toBeNull();
    });

    it("renders with closed groups when the stored state is unreadable", () => {
      window.localStorage.setItem("sidebar.groups.v1", "{not json");
      renderApp("/shots");
      expect(groupList("Machine").button).toHaveAttribute("aria-expanded", "false");
    });

    it("lists a grouped page's chord in the shortcut sheet", async () => {
      const user = setupUser();
      renderApp();
      await user.keyboard("?");
      expect(await screen.findByText("Go to Sets")).toBeInTheDocument();
      expect(screen.getByText("Go to Settings")).toBeInTheDocument();
      expect(screen.queryByText("Go to Device")).not.toBeInTheDocument();
    });
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

  it("navigates to the first settings page on `g ,`", async () => {
    const user = setupUser();
    renderApp("/shots");
    await user.keyboard("g,");
    await waitFor(() => expect(getSettings).toHaveBeenCalled());
    expect(screen.getByRole("heading", { name: "Machine access" })).toBeInTheDocument();
  });

  describe("the Settings group", () => {
    it("is a closed disclosure away from settings, and opens on a click", async () => {
      const user = setupUser();
      renderApp("/shots");
      const nav = within(screen.getByTestId("sidebar"));

      const settings = nav.getByRole("button", { name: /^Settings/ });
      expect(settings).toHaveAttribute("aria-expanded", "false");
      const list = document.getElementById(String(settings.getAttribute("aria-controls")));
      expect(list).not.toBeVisible();

      await user.click(settings);

      expect(settings).toHaveAttribute("aria-expanded", "true");
      expect(list).toBeVisible();
      const pages = within(list as HTMLElement).getAllByRole("link");
      // The settings pages, with the knowledge base just before Prompts.
      const paths = SETTINGS_PAGES.flatMap((p) =>
        p.id === "prompts" ? ["/knowledge", `/settings/${p.id}`] : [`/settings/${p.id}`],
      );
      expect(pages.map((link) => link.getAttribute("href"))).toEqual(paths);
      // Ordered by how likely a page is to need editing: the two nothing works
      // without first, what most people never touch or do once last.
      expect(pages.map((link) => link.querySelector("span")?.textContent)).toEqual([
        "Machine access",
        "LLM",
        "Authentication",
        "Knowledge",
        "Prompts",
        "Profile safety",
        "System",
        "Import",
      ]);
    });

    it("starts open on a settings page, with that page marked", () => {
      renderApp("/settings/llm");
      const nav = within(screen.getByTestId("sidebar"));
      expect(nav.getByRole("button", { name: /^Settings/ })).toHaveAttribute(
        "aria-expanded",
        "true",
      );
      const current = nav.getAllByRole("link", { current: "page" });
      expect(current.map((link) => link.textContent)).toEqual(["LLM"]);
    });

    it("goes to a page from the list", async () => {
      const user = setupUser();
      renderApp("/shots");
      const nav = within(screen.getByTestId("sidebar"));
      await user.click(nav.getByRole("button", { name: /^Settings/ }));
      await user.click(nav.getByRole("link", { name: "Profile safety" }));
      expect(await screen.findByRole("heading", { name: "Profile safety" })).toBeInTheDocument();
    });

    it("opens on the knowledge base, which keeps its own address and chord", async () => {
      const user = setupUser();
      renderApp("/shots");
      await user.keyboard("gk");
      expect(await screen.findByRole("heading", { name: "Knowledge" })).toBeInTheDocument();

      const nav = within(screen.getByTestId("sidebar"));
      expect(nav.getByRole("button", { name: /^Settings/ })).toHaveAttribute(
        "aria-expanded",
        "true",
      );
      const current = nav.getAllByRole("link", { current: "page" });
      expect(current.map((link) => link.getAttribute("href"))).toEqual(["/knowledge"]);
    });

    it("redirects the bare settings path to the first page", async () => {
      renderApp("/settings");
      expect(await screen.findByRole("heading", { name: "Machine access" })).toBeInTheDocument();
    });
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
    renderApp("/settings/machine#connection");

    const host = await screen.findByLabelText("Gaggimate host");
    await user.click(host);
    await user.keyboard("gs");

    // Still on Settings, and the letters landed in the box: a shortcut that
    // fires mid-sentence is worse than no shortcut at all.
    expect(screen.getByRole("heading", { name: "Machine access" })).toBeInTheDocument();
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

  it("redirects the retired drafts route to the staged section", async () => {
    // The queue is a section of the profiles page now, and the anchor is what
    // makes the redirect land on it rather than at the top.
    renderApp("/drafts");
    expect(await screen.findByRole("heading", { name: "Profiles" })).toBeInTheDocument();
  });

  it("shows a 404 page inside the shell for an unknown route", async () => {
    renderApp("/nope");
    expect(await screen.findByText("No such page")).toBeInTheDocument();
    // Still inside the shell, so the user can navigate out of it.
    expect(screen.getAllByRole("navigation", { name: "Main" }).length).toBeGreaterThan(0);
  });

  // ── the rail ──────────────────────────────────────────────────────

  describe("the collapsible rail", () => {
    it("folds to an icon rail on the button and remembers it", async () => {
      const user = setupUser();
      renderApp();

      const sidebar = screen.getByTestId("sidebar");
      expect(sidebar).toHaveAttribute("data-collapsed", "false");
      const collapse = screen.getByRole("button", { name: "Collapse sidebar" });
      expect(collapse).toHaveAttribute("aria-expanded", "true");
      expect(collapse).toHaveAttribute("aria-controls", "sidebar-nav");

      await user.click(collapse);

      expect(sidebar).toHaveAttribute("data-collapsed", "true");
      expect(window.localStorage.getItem("sidebar.collapsed.v1")).toBe("true");
      const expand = screen.getByRole("button", { name: "Expand sidebar" });
      expect(expand).toHaveAttribute("aria-expanded", "false");

      await user.click(expand);

      expect(sidebar).toHaveAttribute("data-collapsed", "false");
      expect(window.localStorage.getItem("sidebar.collapsed.v1")).toBe("false");
    });

    it("starts folded when that is what was stored", () => {
      window.localStorage.setItem("sidebar.collapsed.v1", "true");
      renderApp();
      // Read at mount rather than in an effect, so the first paint is already
      // the right width instead of expanding and then folding.
      expect(screen.getByTestId("sidebar")).toHaveAttribute("data-collapsed", "true");
    });

    it("toggles on the `[` chord", async () => {
      const user = setupUser();
      renderApp();

      await user.keyboard("{[}");
      expect(screen.getByTestId("sidebar")).toHaveAttribute("data-collapsed", "true");

      await user.keyboard("{[}");
      expect(screen.getByTestId("sidebar")).toHaveAttribute("data-collapsed", "false");
    });

    it("keeps every destination named and marked while it is folded", async () => {
      const user = setupUser();
      renderApp("/beans");

      await user.click(screen.getByRole("button", { name: "Collapse sidebar" }));

      const nav = screen.getByTestId("sidebar").querySelector("nav");
      expect(nav).not.toBeNull();
      // Hidden visually, present for assistive tech: the label is still in the
      // accessible name of every entry, with its chord when it has one. A
      // group is a button rather than a link, since it opens its list.
      const named = (entry: { label: string; shortcutLabel?: string }) =>
        entry.shortcutLabel ? `${entry.label} (${entry.shortcutLabel})` : entry.label;
      for (const link of NAV_LINKS) {
        expect(
          within(nav as HTMLElement).getByRole(isNavGroup(link) ? "button" : "link", {
            name: named(link),
          }),
        ).toBeInTheDocument();
      }
      // And the active entry is still the active entry.
      const current = within(nav as HTMLElement).getAllByRole("link", { current: "page" });
      expect(current.map((el) => el.getAttribute("aria-label"))).toEqual(["Beans (g b)"]);
    });

    it("names the settings pages on the rail too", async () => {
      window.localStorage.setItem("sidebar.collapsed.v1", "true");
      renderApp("/settings/system");
      const nav = within(screen.getByTestId("sidebar"));
      for (const page of SETTINGS_PAGES) {
        expect(nav.getByRole("link", { name: page.label })).toBeInTheDocument();
      }
      const current = nav.getAllByRole("link", { current: "page" });
      expect(current.map((el) => el.getAttribute("aria-label"))).toEqual(["System"]);
    });

    it("keeps the nav id unique with the mobile sheet open", async () => {
      // `NavItems` renders twice — the rail and the sheet — and the toggle's
      // `aria-controls` names one of them. Two elements sharing that id would
      // make the reference ambiguous for anything that resolves it.
      const user = setupUser();
      renderApp();

      // The toggle names the rail's nav. Asserted before the sheet opens: the
      // sheet is a modal, so radix hides the rest of the page from the
      // accessibility tree while it is up.
      expect(screen.getByRole("button", { name: "Collapse sidebar" })).toHaveAttribute(
        "aria-controls",
        "sidebar-nav",
      );

      await user.click(screen.getByRole("button", { name: "Open navigation" }));
      await screen.findByRole("dialog");

      expect(document.querySelectorAll("#sidebar-nav")).toHaveLength(1);
      // And the one that is left is the rail's, which is what the toggle names.
      expect(screen.getByTestId("sidebar").querySelector("#sidebar-nav")).not.toBeNull();
      // The sheet still renders every destination; it just does not claim the id.
      const sheetNav = within(screen.getByRole("dialog")).getByRole("navigation", {
        name: "Main",
      });
      expect(sheetNav).not.toHaveAttribute("id");
      expect(sheetNav).toHaveTextContent("Shots");
    });

    it("lists the fold chord in the shortcut sheet", async () => {
      const user = setupUser();
      renderApp();
      await user.keyboard("?");
      expect(await screen.findByText(/Fold the sidebar to an icon rail/)).toBeInTheDocument();
    });
  });
});
