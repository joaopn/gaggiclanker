import { screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { SettingsMap } from "@/api/types";
import { DeviceWritesSwitch, WRITES_ON_SENTENCE } from "@/components/DeviceWritesSwitch";
import { queryKeys } from "@/lib/queryKeys";
import { renderWithQueryClient, setupUser } from "@/test/renderWithQueryClient";

const { getSettings, patchSettings } = vi.hoisted(() => ({
  getSettings: vi.fn(),
  patchSettings: vi.fn(),
}));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  getSettings,
  patchSettings,
}));

function settings(enabled: boolean): SettingsMap {
  return {
    deviceWritesEnabled: {
      key: "deviceWritesEnabled",
      type: "bool",
      secret: false,
      readonly: false,
      value: enabled,
      default: false,
      override: enabled ? true : null,
      source: enabled ? "database" : "default",
    },
  } as unknown as SettingsMap;
}

/** A server that keeps what it was told, so the refetch after a change agrees. */
function serverAcceptsChanges() {
  patchSettings.mockImplementation(async (patch: { deviceWritesEnabled: boolean }) => {
    const next = settings(patch.deviceWritesEnabled);
    getSettings.mockResolvedValue(next);
    return next;
  });
}

const toggle = () => screen.getByRole("switch", { name: "Machine writes" });

async function renderSwitch(enabled: boolean) {
  getSettings.mockResolvedValue(settings(enabled));
  const view = renderWithQueryClient(<DeviceWritesSwitch />);
  await waitFor(() => expect(toggle()).toBeEnabled());
  return view;
}

describe("DeviceWritesSwitch", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("shows whether writes are on or off, from the served setting", async () => {
    const { unmount } = await renderSwitch(false);
    expect(toggle()).toHaveAttribute("aria-checked", "false");
    expect(toggle()).toHaveAttribute("data-state", "off");
    expect(toggle()).toHaveTextContent("off");
    unmount();

    await renderSwitch(true);
    expect(toggle()).toHaveAttribute("aria-checked", "true");
    expect(toggle()).toHaveTextContent("on");
  });

  it("is icon-only below sm, so the header fits at phone width", async () => {
    await renderSwitch(false);
    // jsdom has no layout; what can be pinned is the classes that do the work.
    const label = toggle().querySelector("span");
    expect(label).toHaveClass("max-sm:sr-only");
    expect(label?.querySelector("span")).toHaveClass("hidden", "lg:inline");
    // The state survives without the text: shape, colour and aria-checked.
    expect(toggle()).toHaveAttribute("aria-checked", "false");
    expect(toggle().querySelector("svg")).toHaveClass("lucide-pen-off");
  });

  it("is a plain switch: no popup attributes on it", async () => {
    const user = setupUser();
    await renderSwitch(false);
    await user.click(toggle());
    expect(toggle()).not.toHaveAttribute("aria-expanded");
    expect(toggle()).not.toHaveAttribute("aria-haspopup");
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("asks before turning on, and says what on allows today", async () => {
    const user = setupUser();
    await renderSwitch(false);

    await user.click(toggle());

    expect(patchSettings).not.toHaveBeenCalled();
    const panel = screen.getByRole("dialog", { name: "Writes to the machine" });
    expect(panel).toHaveTextContent(WRITES_ON_SENTENCE);
    expect(panel.textContent).toMatch(/only lets you push a profile .* roll one back/);
    // Nothing here may promise a sync that does not exist yet.
    expect(panel.textContent ?? "").not.toMatch(/sync|every pull|automatic/i);
  });

  it("sends {deviceWritesEnabled: true} once confirmed", async () => {
    const user = setupUser();
    await renderSwitch(false);
    serverAcceptsChanges();

    await user.click(toggle());
    await user.click(screen.getByRole("button", { name: "Turn on" }));

    await waitFor(() => expect(patchSettings).toHaveBeenCalledWith({ deviceWritesEnabled: true }));
    expect(patchSettings).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(toggle()).toHaveAttribute("aria-checked", "true"));
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("sends nothing when the confirmation is cancelled", async () => {
    const user = setupUser();
    await renderSwitch(false);

    await user.click(toggle());
    await user.click(screen.getByRole("button", { name: "Cancel" }));

    expect(screen.queryByRole("dialog")).toBeNull();
    expect(patchSettings).not.toHaveBeenCalled();
    expect(toggle()).toHaveAttribute("aria-checked", "false");
  });

  it("turns off immediately, with no confirmation", async () => {
    const user = setupUser();
    await renderSwitch(true);
    serverAcceptsChanges();

    await user.click(toggle());

    expect(screen.queryByRole("dialog")).toBeNull();
    await waitFor(() => expect(patchSettings).toHaveBeenCalledWith({ deviceWritesEnabled: false }));
    await waitFor(() => expect(toggle()).toHaveAttribute("aria-checked", "false"));
  });

  it("says so when the change is refused, and leaves the switch as it was", async () => {
    const user = setupUser();
    await renderSwitch(false);
    patchSettings.mockRejectedValue(new Error("Sign in to change settings"));

    await user.click(toggle());
    await user.click(screen.getByRole("button", { name: "Turn on" }));

    const alert = await screen.findByTestId("device-writes-error");
    expect(alert).toHaveTextContent("Sign in to change settings");
    expect(alert).toHaveTextContent("Writes are still off");
    // Focus moves to Close rather than falling to <body> with the buttons gone.
    expect(screen.getByRole("button", { name: "Close" })).toHaveFocus();
    expect(toggle()).toHaveAttribute("aria-checked", "false");
  });

  it("shows a failed turn-off too, with writes still on", async () => {
    const user = setupUser();
    await renderSwitch(true);
    patchSettings.mockRejectedValue(new Error("Failed to fetch"));

    await user.click(toggle());

    const alert = await screen.findByTestId("device-writes-error");
    expect(alert).toHaveTextContent("Writes are still on");
    expect(toggle()).toHaveAttribute("aria-checked", "true");
  });

  it("invalidates every query that reads the setting, after a change and after a failure", async () => {
    const user = setupUser();
    const { queryClient } = await renderSwitch(false);
    const spy = vi.spyOn(queryClient, "invalidateQueries");
    serverAcceptsChanges();

    await user.click(toggle());
    await user.click(screen.getByRole("button", { name: "Turn on" }));
    await waitFor(() => expect(toggle()).toHaveAttribute("aria-checked", "true"));

    const keys = spy.mock.calls.map(([filters]) => filters?.queryKey);
    expect(keys).toContainEqual(queryKeys.settings.all);
    expect(keys).toContainEqual(queryKeys.device.writes());

    spy.mockClear();
    patchSettings.mockRejectedValue(new Error("nope"));
    await user.click(toggle());
    await waitFor(() => expect(screen.getByTestId("device-writes-error")).toBeInTheDocument());
    const failedKeys = spy.mock.calls.map(([filters]) => filters?.queryKey);
    expect(failedKeys).toContainEqual(queryKeys.settings.all);
    expect(failedKeys).toContainEqual(queryKeys.device.writes());
  });
});
