import { screen, waitFor, within } from "@testing-library/react";
import { toast } from "sonner";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ProfileVersionListData } from "@/api/types";
import { ProfilesPage } from "@/pages/ProfilesPage";
import { baseProfile, draft, draftDetail } from "@/test/draftFixtures";
import { renderWithQueryClient, setupUser } from "@/test/renderWithQueryClient";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
  Toaster: () => null,
}));

const {
  getProfiles,
  getProfileVersions,
  getProfileVersion,
  getProfileDrafts,
  getProfileDraft,
  getDeviceWrites,
  createProfileDraft,
  importFiles,
} = vi.hoisted(() => ({
  getProfiles: vi.fn(),
  getProfileVersions: vi.fn(),
  getProfileVersion: vi.fn(),
  getProfileDrafts: vi.fn(),
  getProfileDraft: vi.fn(),
  getDeviceWrites: vi.fn(),
  createProfileDraft: vi.fn(),
  importFiles: vi.fn(),
}));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  getProfiles,
  getProfileVersions,
  getProfileVersion,
  getProfileDrafts,
  getProfileDraft,
  getDeviceWrites,
  createProfileDraft,
  importFiles,
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
  getProfileVersion.mockResolvedValue({
    ...versions.items[0],
    profile: baseProfile(),
  });
  getProfileDrafts.mockResolvedValue({ items: [draft()] });
  getProfileDraft.mockResolvedValue(draftDetail());
  getDeviceWrites.mockResolvedValue({ enabled: true, items: [] });
  createProfileDraft.mockResolvedValue(draft());
  getProfiles.mockResolvedValue({
    items: [
      {
        device_id: "9bar",
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
    // And it is addressable, which is what an import result's link points at.
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

describe("ProfilesPage staging queue", () => {
  it("shows what is open by default", async () => {
    renderWithQueryClient(<ProfilesPage />);
    expect(await screen.findByTestId("draft-list")).toBeInTheDocument();
    expect(getProfileDrafts).toHaveBeenCalledWith({ open: true });
  });

  it("can be switched to everything, including what has already been pushed", async () => {
    const user = setupUser();
    renderWithQueryClient(<ProfilesPage />);
    await screen.findByTestId("draft-list");

    await user.click(screen.getByRole("button", { name: /Show everything/ }));

    expect(getProfileDrafts).toHaveBeenLastCalledWith({});
  });

  it("says so when writing to the machine is switched off", async () => {
    // The banner is here rather than only on Settings because this is where
    // somebody is standing when an approved draft will not push.
    getDeviceWrites.mockResolvedValue({ enabled: false, items: [] });
    renderWithQueryClient(<ProfilesPage />);

    const banner = await screen.findByTestId("writes-disabled-banner");
    expect(banner).toHaveTextContent("switched off");
    expect(within(banner).getByRole("link", { name: /Settings/ })).toHaveAttribute(
      "href",
      "/settings",
    );
  });

  it("does not nag when writes are on", async () => {
    renderWithQueryClient(<ProfilesPage />);
    await screen.findByTestId("draft-list");
    expect(screen.queryByTestId("writes-disabled-banner")).not.toBeInTheDocument();
  });

  it("stays quiet about switched-off writes when nothing is still open", async () => {
    // "Show everything" widens the list to drafts that have already been
    // pushed or discarded. Those are not blocked by anything, and a banner
    // that fires on them says the queue is stuck when it is empty.
    const user = setupUser();
    getDeviceWrites.mockResolvedValue({ enabled: false, items: [] });
    getProfileDrafts.mockImplementation(async (params: { open?: boolean } = {}) =>
      params.open ? { items: [] } : { items: [draft({ status: "pushed" })] },
    );
    renderWithQueryClient(<ProfilesPage />);

    await screen.findByTestId("staged-empty");
    await user.click(screen.getByRole("button", { name: /Show everything/ }));

    await screen.findByTestId("draft-list");
    expect(screen.queryByTestId("writes-disabled-banner")).not.toBeInTheDocument();
  });

  it("stays quiet about switched-off writes when nothing is staged", async () => {
    // Nothing is queued, so nothing is blocked. A warning that is always on is
    // one nobody reads by the second week.
    getDeviceWrites.mockResolvedValue({ enabled: false, items: [] });
    getProfileDrafts.mockResolvedValue({ items: [] });
    renderWithQueryClient(<ProfilesPage />);

    await screen.findByTestId("staged-empty");
    expect(screen.queryByTestId("writes-disabled-banner")).not.toBeInTheDocument();
  });

  it("points an empty queue at the two ways of filling it", async () => {
    getProfileDrafts.mockResolvedValue({ items: [] });
    renderWithQueryClient(<ProfilesPage />);

    expect(await screen.findByTestId("staged-empty")).toHaveTextContent(
      "Stage a version below, or accept a profile suggestion from an analysis.",
    );
  });

  it("is anchored, so a link from an analysis lands on it", async () => {
    const scrollIntoView = vi.fn();
    // jsdom has no layout, so the method does not exist at all.
    Element.prototype.scrollIntoView = scrollIntoView;

    renderWithQueryClient(<ProfilesPage />, { initialEntries: ["/profiles#staged"] });

    await screen.findByTestId("draft-list");
    await waitFor(() => expect(scrollIntoView).toHaveBeenCalled());
  });
});

describe("ProfilesPage staging a version", () => {
  it("stages a version exactly as it stands", async () => {
    const user = setupUser();
    renderWithQueryClient(<ProfilesPage />);

    const rows = await screen.findAllByTestId("profile-version-row");
    await user.click(within(rows[0]).getByTestId("stage-as-is"));

    // The document is read first: the versions list carries summaries only.
    await waitFor(() => expect(getProfileVersion).toHaveBeenCalledWith(7));
    expect(createProfileDraft).toHaveBeenCalledWith({
      base_version_id: 7,
      profile: baseProfile(),
      // "No edits" rather than "unchanged": nobody edited it, but the safety
      // policy may still have moved a number, and the next test is why that
      // distinction is not pedantry.
      change_summary: "Staged from 9 Bar Espresso, no edits",
    });
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith("Staged for the machine"));
  });

  it("says so when the safety policy moved a value on the way", async () => {
    // A version mirrored off a machine at 118 °C is stored at 100: nobody
    // edited it and it is still not the document that was posted. The card
    // lists what moved; the toast is what stops the person scrolling past it.
    const user = setupUser();
    createProfileDraft.mockResolvedValue(
      draft({
        clamp_changes: [
          { path: "temperature", from: 118, to: 100, reason: "above the policy maximum" },
        ],
      }),
    );

    renderWithQueryClient(<ProfilesPage />);
    const rows = await screen.findAllByTestId("profile-version-row");
    await user.click(within(rows[0]).getByTestId("stage-as-is"));

    await waitFor(() =>
      expect(toast.success).toHaveBeenCalledWith("Staged — the safety policy moved 1 value"),
    );
  });

  it("uploads a profile export and reports what it did", async () => {
    const user = setupUser();
    importFiles.mockResolvedValue({
      created: 1,
      updated: 0,
      skipped: 0,
      failed: 0,
      items: [
        {
          filename: "cremina.json",
          kind: "profile",
          status: "created",
          message: "profile version 9",
          shot_id: null,
          device_id: null,
          profile_version_id: 9,
          label: "Cremina v2",
          quarantined: false,
        },
      ],
    });

    renderWithQueryClient(<ProfilesPage />);
    await screen.findAllByTestId("profile-version-row");

    const file = new File(['{"label":"Cremina v2"}'], "cremina.json", {
      type: "application/json",
    });
    await user.upload(screen.getByTestId("profile-upload-input"), file);

    await waitFor(() => expect(importFiles).toHaveBeenCalled());
    const result = await screen.findByTestId("import-result");
    expect(result).toHaveTextContent("1 imported");
    await user.click(within(result).getByTestId("import-result-toggle"));
    // The version, not the mirror: a profile from a file is on no machine.
    expect(within(result).getByRole("link", { name: "Cremina v2" })).toHaveAttribute(
      "href",
      "/profiles#version-9",
    );
  });
});
