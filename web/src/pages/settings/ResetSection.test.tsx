import { screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderWithQueryClient, setupUser } from "@/test/renderWithQueryClient";

const { resetApp, getHealth } = vi.hoisted(() => ({
  resetApp: vi.fn(),
  getHealth: vi.fn(),
}));

vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  resetApp,
  getHealth,
}));

import { ApiClientError } from "@/api/client";
import { ResetSection, resetRefusalText } from "@/pages/settings/ResetSection";

const IDLE =
  "Puts the app back to a fresh install: every shot, Set, bean, profile, chat, insight, edited prompt and setting is deleted. Nothing on the machine changes.";

describe("ResetSection", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("says what a reset does and offers only the first button", () => {
    renderWithQueryClient(<ResetSection />);

    expect(screen.getByText(IDLE)).toBeVisible();
    expect(
      screen.getByText("Download a backup above first if you want to keep any of it."),
    ).toBeVisible();
    expect(screen.getByRole("button", { name: "Reset the app…" })).toBeVisible();
    expect(screen.queryByRole("button", { name: "Reset and restart" })).not.toBeInTheDocument();
    expect(resetApp).not.toHaveBeenCalled();
  });

  it("asks again inline, with the three consequences in exact words", async () => {
    const user = setupUser();
    renderWithQueryClient(<ResetSection />);

    await user.click(screen.getByRole("button", { name: "Reset the app…" }));

    expect(
      screen.getByText("This deletes everything in the app and restarts it. It cannot be undone."),
    ).toBeVisible();
    const items = screen.getAllByRole("listitem").map((item) => item.textContent);
    expect(items).toEqual([
      "Your API keys and tokens are deleted.",
      "Sign-in is switched off: anyone who can reach the app can use it until you set a password again.",
      "The app restarts, which takes a few seconds.",
    ]);
    expect(screen.getByRole("button", { name: "Reset and restart" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeVisible();
    expect(resetApp).not.toHaveBeenCalled();
  });

  it("Cancel goes back to the start and sends nothing", async () => {
    const user = setupUser();
    renderWithQueryClient(<ResetSection />);
    await user.click(screen.getByRole("button", { name: "Reset the app…" }));

    await user.click(screen.getByRole("button", { name: "Cancel" }));

    expect(screen.getByText(IDLE)).toBeVisible();
    expect(screen.queryByRole("button", { name: "Reset and restart" })).not.toBeInTheDocument();
    expect(resetApp).not.toHaveBeenCalled();
  });

  it("the button sends the request once, however fast it is pressed, and covers the page", async () => {
    const user = setupUser();
    resetApp.mockResolvedValue({ restarting: true });
    getHealth.mockResolvedValue({ status: "ok", version: "0.1.0", database: "ok" });
    renderWithQueryClient(<ResetSection />);
    await user.click(screen.getByRole("button", { name: "Reset the app…" }));

    await user.dblClick(screen.getByRole("button", { name: "Reset and restart" }));

    expect(await screen.findByText("Resetting… the app is restarting.")).toBeVisible();
    expect(screen.getByText("This page reloads by itself.")).toBeVisible();
    expect(screen.getByTestId("restart-overlay")).toBeVisible();
    expect(resetApp).toHaveBeenCalledTimes(1);
  });

  it("two presses before the page can re-render still send one request", async () => {
    const user = setupUser();
    resetApp.mockReturnValue(new Promise(() => {}));
    renderWithQueryClient(<ResetSection />);
    await user.click(screen.getByRole("button", { name: "Reset the app…" }));
    const button = screen.getByRole("button", { name: "Reset and restart" });

    // Straight at the DOM node, twice in one tick: no act() between them, so React has not
    // yet re-rendered the button as disabled.
    button.click();
    button.click();

    expect(resetApp).toHaveBeenCalledTimes(1);
  });

  it("a refusal for work in flight says to wait, in the same place, and changes nothing", async () => {
    const user = setupUser();
    resetApp.mockRejectedValue(new ApiClientError("busy", { code: "RESET_BUSY", status: 409 }));
    renderWithQueryClient(<ResetSection />);
    await user.click(screen.getByRole("button", { name: "Reset the app…" }));

    await user.click(screen.getByRole("button", { name: "Reset and restart" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "A sync, a chat answer or a review is running. Try again when it finishes.",
    );
    expect(screen.queryByTestId("restart-overlay")).not.toBeInTheDocument();
    // Still on the confirm, and the button works again: a second press is a second request.
    expect(screen.getByRole("button", { name: "Reset and restart" })).toBeEnabled();
    resetApp.mockResolvedValue({ restarting: true });
    getHealth.mockResolvedValue({ status: "ok", version: "0.1.0", database: "ok" });
    await user.click(screen.getByRole("button", { name: "Reset and restart" }));
    expect(resetApp).toHaveBeenCalledTimes(2);
    expect(await screen.findByTestId("restart-overlay")).toBeVisible();
  });
});

describe("resetRefusalText", () => {
  it("names a pending restore or reset and falls back to the server's words", () => {
    expect(
      resetRefusalText(new ApiClientError("x", { code: "RESTORE_PENDING", status: 409 })),
    ).toBe("A restore is already under way.");
    expect(resetRefusalText(new ApiClientError("x", { code: "RESET_PENDING", status: 409 }))).toBe(
      "A reset is already under way.",
    );
    expect(resetRefusalText(new Error("disk full"))).toBe("The app can't be reset: disk full");
  });
});
