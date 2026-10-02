import { screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { SettingsMap } from "@/api/types";
import { DeviceWritesSwitch, WRITES_ON_SENTENCE } from "@/components/DeviceWritesSwitch";
import { queryKeys } from "@/lib/queryKeys";
import { boardAction, boardView } from "@/test/boardFixtures";
import { renderWithQueryClient, setupUser } from "@/test/renderWithQueryClient";

const { getSettings, patchSettings, getProfileBoard } = vi.hoisted(() => ({
  getSettings: vi.fn(),
  patchSettings: vi.fn(),
  getProfileBoard: vi.fn(),
}));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  getSettings,
  patchSettings,
  getProfileBoard,
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
    // Before the board is adopted, read from the machine just now.
    getProfileBoard.mockResolvedValue(
      boardView({ adopted: false, rows: [], machine_source: "machine" }),
    );
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

  it("spans the viewport under sm: the wrapper is static and the panel has side insets", async () => {
    const user = setupUser();
    await renderSwitch(false);
    await user.click(toggle());
    // Below sm the panel is positioned against the sticky header, not against
    // the switch's own wrapper; without these it collapses to a sliver.
    expect(toggle().parentElement).toHaveClass("max-sm:static");
    expect(screen.getByRole("dialog")).toHaveClass("max-sm:inset-x-4", "max-sm:w-auto");
  });

  it("is a plain switch: no popup attributes on it", async () => {
    const user = setupUser();
    await renderSwitch(false);
    await user.click(toggle());
    expect(toggle()).not.toHaveAttribute("aria-expanded");
    expect(toggle()).not.toHaveAttribute("aria-haspopup");
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("asks before turning on, and says what on means: every sync makes the machine hold the profiles that are on", async () => {
    const user = setupUser();
    await renderSwitch(false);

    await user.click(toggle());

    expect(patchSettings).not.toHaveBeenCalled();
    const panel = screen.getByRole("dialog", { name: "Writes to the machine" });
    expect(panel).toHaveTextContent(WRITES_ON_SENTENCE);
    expect(panel).toHaveTextContent(
      "every sync makes the machine hold exactly the profiles that are on",
    );
    expect(panel).toHaveTextContent("removes the profiles that are switched off");
    expect(panel).toHaveTextContent("the machine's own included");
    expect(panel).toHaveTextContent("edited on the machine is left alone");
    expect(panel.textContent ?? "").not.toMatch(/never removes or overwrites/);
    // The sentence that said the switch only allowed a person's push is gone.
    expect(panel.textContent ?? "").not.toMatch(
      /only lets you push|from the Profiles page|roll one back/,
    );
  });

  describe("the preview of the next sync", () => {
    it("before the board is adopted says the first sync writes nothing", async () => {
      const user = setupUser();
      getProfileBoard.mockResolvedValue(
        boardView({
          adopted: false,
          rows: [],
          machine_source: "machine",
          actions: [
            boardAction({ kind: "adopt", label: "A" }),
            boardAction({ kind: "adopt", label: "B" }),
          ],
        }),
      );
      await renderSwitch(false);

      await user.click(toggle());

      const preview = await screen.findByTestId("writes-preview-first");
      expect(preview).toHaveTextContent(
        "The first sync takes the machine's profiles into the list and writes nothing.",
      );
      expect(preview).toHaveTextContent("2 profiles are on the machine to take");
      expect(getProfileBoard).toHaveBeenCalledWith(true);
    });

    it("after adoption gives the counts by kind and the list", async () => {
      const user = setupUser();
      getProfileBoard.mockResolvedValue(
        boardView({
          machine_source: "machine",
          actions: [
            boardAction({ kind: "push", label: "Londinium [AI]" }),
            boardAction({ kind: "remove", label: "Old [AI]", device_id: "o1" }),
            boardAction({ kind: "home_screen", label: "9 Bar", on: false }),
            boardAction({ kind: "leave", label: "Mine", detail: "not the app's" }),
          ],
        }),
      );
      await renderSwitch(false);

      await user.click(toggle());

      const counts = await screen.findByTestId("writes-preview-counts");
      expect(counts).toHaveTextContent(
        "The next sync would put 1 profile on the machine, remove 1 profile from it, change 1 star, and leave 1 on the machine.",
      );
      const list = screen.getByTestId("writes-preview-list");
      expect(list).toHaveTextContent("Put Londinium [AI] on the machine");
      expect(list).toHaveTextContent("Remove Old [AI] from the machine");
      expect(list).toHaveTextContent("Take the star off 9 Bar");
      expect(screen.queryByTestId("writes-preview-stale")).toBeNull();
    });

    it("says profiles in conflict are left alone", async () => {
      const user = setupUser();
      getProfileBoard.mockResolvedValue(
        boardView({
          machine_source: "machine",
          reports: [boardAction({ kind: "report", row_id: 1, reason: "conflict" })],
        }),
      );
      await renderSwitch(false);

      await user.click(toggle());

      expect(await screen.findByTestId("writes-preview-conflicts")).toHaveTextContent(
        "1 profile is in conflict with the machine and will be left alone",
      );
    });

    it("says nothing would change when the machine already matches the list", async () => {
      const user = setupUser();
      getProfileBoard.mockResolvedValue(boardView({ machine_source: "machine" }));
      await renderSwitch(false);

      await user.click(toggle());

      expect(await screen.findByTestId("writes-preview-none")).toHaveTextContent(
        "the next sync would change nothing",
      );
    });

    it("falls back to the last sync and says so when the machine cannot be read", async () => {
      const user = setupUser();
      getProfileBoard.mockResolvedValue(
        boardView({
          machine_source: "mirror",
          actions: [boardAction({ kind: "push", label: "Londinium [AI]" })],
        }),
      );
      await renderSwitch(false);

      await user.click(toggle());

      expect(await screen.findByTestId("writes-preview-stale")).toHaveTextContent(
        "The machine could not be read just now, so this is from the last sync.",
      );
      expect(screen.getByTestId("writes-preview-list")).toHaveTextContent(
        "Put Londinium [AI] on the machine",
      );
    });

    it("does not say the machine matches the board when it could not be read", async () => {
      const user = setupUser();
      getProfileBoard.mockResolvedValue(boardView({ machine_source: "mirror", actions: [] }));
      await renderSwitch(false);

      await user.click(toggle());

      expect(await screen.findByTestId("writes-preview-unknown")).toHaveTextContent(
        "no telling what the next sync would do",
      );
      expect(screen.queryByTestId("writes-preview-none")).toBeNull();
    });

    it("still lets you turn on when the board cannot be read at all", async () => {
      const user = setupUser();
      getProfileBoard.mockRejectedValue(new Error("boom"));
      await renderSwitch(false);

      await user.click(toggle());

      expect(await screen.findByTestId("writes-preview")).toHaveTextContent(
        "could not be read just now",
      );
      expect(screen.getByRole("button", { name: "Turn on" })).toBeEnabled();
    });

    it("says a paused board writes nothing until resumed", async () => {
      const user = setupUser();
      getProfileBoard.mockResolvedValue(
        boardView({ machine_source: "machine", paused: "the machine looks reset" }),
      );
      await renderSwitch(false);

      await user.click(toggle());

      expect(await screen.findByTestId("writes-preview-paused")).toHaveTextContent(
        "syncs write nothing until you resume them on the Profiles page",
      );
    });

    it("does not read the machine until the confirmation is open", async () => {
      await renderSwitch(false);
      expect(getProfileBoard).not.toHaveBeenCalled();
    });

    it("does not read the machine to turn writes off", async () => {
      const user = setupUser();
      await renderSwitch(true);
      serverAcceptsChanges();
      await user.click(toggle());
      await waitFor(() => expect(patchSettings).toHaveBeenCalled());
      expect(getProfileBoard).not.toHaveBeenCalled();
    });
  });

  it("keeps the panel inside the viewport: height-capped, scrolling inside, full width under sm", async () => {
    const user = setupUser();
    await renderSwitch(false);
    await user.click(toggle());
    expect(screen.getByRole("dialog")).toHaveClass(
      "max-h-[calc(100dvh-5rem)]",
      "overflow-y-auto",
      "max-sm:inset-x-4",
      "max-sm:w-auto",
    );
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
    expect(keys).toContainEqual(queryKeys.device.all);
    // And what the switch changes besides the setting: the board's `writes_enabled` and
    // its preview, the sync status, the drafts and the profile mirror.
    for (const key of [
      queryKeys.board.all,
      queryKeys.sync.all,
      queryKeys.drafts.all,
      queryKeys.profiles.all,
    ]) {
      expect(keys).toContainEqual(key);
    }

    spy.mockClear();
    patchSettings.mockRejectedValue(new Error("nope"));
    await user.click(toggle());
    await waitFor(() => expect(screen.getByTestId("device-writes-error")).toBeInTheDocument());
    const failedKeys = spy.mock.calls.map(([filters]) => filters?.queryKey);
    expect(failedKeys).toContainEqual(queryKeys.settings.all);
    expect(failedKeys).toContainEqual(queryKeys.device.all);
    expect(failedKeys).toContainEqual(queryKeys.board.all);
  });
});
