import { screen, waitFor } from "@testing-library/react";
import { toast } from "sonner";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ProfileAutomatch } from "@/components/shots/ProfileAutomatch";
import { renderWithQueryClient, setupUser } from "@/test/renderWithQueryClient";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
  Toaster: () => null,
}));

const { matchShotsByProfile } = vi.hoisted(() => ({
  matchShotsByProfile: vi.fn(),
}));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  matchShotsByProfile,
}));

beforeEach(() => {
  vi.clearAllMocks();
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
  });
});
