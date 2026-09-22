import { screen, waitFor } from "@testing-library/react";
import { toast } from "sonner";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { SettingsMap } from "@/api/types";
import { AUTOMATCH_KEY, ProfileAutomatch } from "@/components/shots/ProfileAutomatch";
import { profileMatchMessage } from "@/hooks/useSets";
import { renderWithQueryClient, setupUser } from "@/test/renderWithQueryClient";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
  Toaster: () => null,
}));

const { getSettings, patchSettings, matchShotsByProfile } = vi.hoisted(() => ({
  getSettings: vi.fn(),
  patchSettings: vi.fn(),
  matchShotsByProfile: vi.fn(),
}));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  getSettings,
  patchSettings,
  matchShotsByProfile,
}));

function settings(value: boolean): SettingsMap {
  return {
    [AUTOMATCH_KEY]: {
      key: AUTOMATCH_KEY,
      type: "bool",
      secret: false,
      readonly: false,
      value,
      default: false,
      override: value ? true : null,
      source: value ? "database" : "default",
      description: "File a new shot under the one Set that brews its profile.",
    },
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  getSettings.mockResolvedValue(settings(false));
});

describe("ProfileAutomatch", () => {
  it("matches every waiting shot from the Shots page and says what happened", async () => {
    const user = setupUser();
    matchShotsByProfile.mockResolvedValue({ matched: 3, ambiguous: 1, unmatched: 0 });
    renderWithQueryClient(<ProfileAutomatch />);

    await user.click(screen.getByRole("button", { name: "Match by profile" }));

    await waitFor(() => expect(matchShotsByProfile).toHaveBeenCalledWith(undefined));
    await waitFor(() =>
      expect(toast.success).toHaveBeenCalledWith(
        "Filed 3 shots · 1 left: more than one Set brews the profile",
      ),
    );
  });

  it("matches only this shot on a shot's page, and is gone once it has a Set", async () => {
    const user = setupUser();
    matchShotsByProfile.mockResolvedValue({ matched: 0, ambiguous: 0, unmatched: 1 });
    const { rerender } = renderWithQueryClient(<ProfileAutomatch shotId={42} />);

    await user.click(screen.getByRole("button", { name: "Match by profile" }));
    await waitFor(() => expect(matchShotsByProfile).toHaveBeenCalledWith([42]));
    await waitFor(() =>
      expect(toast.info).toHaveBeenCalledWith("Nothing filed · 1 left: no Set brews the profile"),
    );

    rerender(<ProfileAutomatch shotId={42} filed />);
    expect(screen.queryByRole("button", { name: "Match by profile" })).toBeNull();
    // The switch is about new shots, so it stays whatever this shot's state.
    expect(screen.getByRole("checkbox", { name: "Automatch new shots" })).toBeInTheDocument();
  });

  it("shows the stored switch and writes only that key", async () => {
    const user = setupUser();
    // The server's answer, and what the re-read after it brings back.
    patchSettings.mockImplementation(async () => {
      getSettings.mockResolvedValue(settings(true));
      return settings(true);
    });
    renderWithQueryClient(<ProfileAutomatch />);

    const box = screen.getByRole("checkbox", { name: "Automatch new shots" });
    // Disabled until the stored value is known, so a click cannot write the
    // opposite of what somebody meant.
    expect(box).toBeDisabled();
    await waitFor(() => expect(box).toBeEnabled());
    expect(box).not.toBeChecked();

    await user.click(box);

    await waitFor(() => expect(patchSettings).toHaveBeenCalled());
    expect(patchSettings.mock.calls[0]?.[0]).toEqual({ [AUTOMATCH_KEY]: true });
    await waitFor(() => expect(box).toBeChecked());
  });

  it("says so when the switch could not be saved", async () => {
    const user = setupUser();
    patchSettings.mockRejectedValue(new Error("offline"));
    renderWithQueryClient(<ProfileAutomatch />);
    const box = screen.getByRole("checkbox", { name: "Automatch new shots" });
    await waitFor(() => expect(box).toBeEnabled());

    await user.click(box);

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith("Could not save: offline"));
    await waitFor(() => expect(box).not.toBeChecked());
  });
});

describe("profileMatchMessage", () => {
  it("names only the outcomes that happened", () => {
    expect(profileMatchMessage({ matched: 1, ambiguous: 0, unmatched: 0 })).toBe("Filed 1 shot");
    expect(profileMatchMessage({ matched: 0, ambiguous: 0, unmatched: 0 })).toBe(
      "No shots were waiting for a Set",
    );
    expect(profileMatchMessage({ matched: 2, ambiguous: 1, unmatched: 4 })).toBe(
      "Filed 2 shots · 1 left: more than one Set brews the profile · 4 left: no Set brews the profile",
    );
  });
});
