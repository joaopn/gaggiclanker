import { screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ProfileVersionListData } from "@/api/types";
import { ProfilesPage } from "@/pages/ProfilesPage";
import { renderWithQueryClient } from "@/test/renderWithQueryClient";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
  Toaster: () => null,
}));

const { getProfiles, getProfileVersions } = vi.hoisted(() => ({
  getProfiles: vi.fn(),
  getProfileVersions: vi.fn(),
}));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  getProfiles,
  getProfileVersions,
}));

const versions: ProfileVersionListData = {
  items: [
    {
      id: 7,
      content_hash: "abcdef0123456789",
      label: "9 Bar Espresso",
      type: "standard",
      utility: false,
      source: "device",
      created_at: "2026-03-01T00:00:00.000Z",
      mirrored: true,
      shot_count: 12,
    },
    {
      id: 9,
      content_hash: "99887766aabbccdd",
      label: "Cremina v2",
      type: "pro",
      utility: false,
      source: "import",
      created_at: "2026-02-01T00:00:00.000Z",
      mirrored: false,
      shot_count: 0,
    },
  ],
  total: 2,
  limit: 200,
  offset: 0,
};

beforeEach(() => {
  vi.clearAllMocks();
  getProfileVersions.mockResolvedValue(versions);
  getProfiles.mockResolvedValue({
    items: [
      {
        device_id: "9bar",
        machine_id: 1,
        current_version_id: 7,
        favorite: true,
        selected: true,
        position: 0,
        first_seen_at: "2026-03-01T00:00:00.000Z",
        last_seen_at: "2026-03-04T00:00:00.000Z",
        deleted_at: null,
        label: "9 Bar Espresso",
        type: "standard",
        utility: false,
        content_hash: "abcdef0123456789",
        shot_count: 12,
      },
    ],
  });
});

describe("ProfilesPage", () => {
  it("lists the mirror with its version hash", async () => {
    renderWithQueryClient(<ProfilesPage />);

    expect(await screen.findAllByText("9 Bar Espresso")).not.toHaveLength(0);
    expect(screen.getByText("selected")).toBeInTheDocument();
    expect(screen.getAllByText("abcdef01").length).toBeGreaterThan(0);
  });

  it("lists standalone versions the machine has never had", async () => {
    // The whole reason `/api/profile-versions` exists: an imported profile is
    // on no device, so the mirror table above would not show it at all.
    renderWithQueryClient(<ProfilesPage />);

    const rows = await screen.findAllByTestId("profile-version-row");
    expect(rows).toHaveLength(2);

    const imported = rows.find((row) => within(row).queryByText("Cremina v2"));
    expect(imported).toBeDefined();
    expect(imported as HTMLElement).toHaveTextContent("imported");
    expect(imported as HTMLElement).toHaveTextContent("not on the machine");
    // And it is addressable, which is what the Import page's link points at.
    expect(imported).toHaveAttribute("id", "version-9");
  });

  it("links a version's shot count at the filtered list", async () => {
    renderWithQueryClient(<ProfilesPage />);

    const rows = await screen.findAllByTestId("profile-version-row");
    const mirrored = rows[0];
    expect(within(mirrored).getByRole("link", { name: "12" })).toHaveAttribute(
      "href",
      "/shots?profile_version_id=7",
    );
    expect(mirrored).toHaveTextContent("mirrored");
  });
});
