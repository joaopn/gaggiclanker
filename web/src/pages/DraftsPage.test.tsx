import { screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { DraftsPage } from "@/pages/DraftsPage";
import { draft, draftDetail } from "@/test/draftFixtures";
import { renderWithQueryClient, setupUser } from "@/test/renderWithQueryClient";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
  Toaster: () => null,
}));

const { getProfileDrafts, getProfileDraft, getDeviceWrites } = vi.hoisted(() => ({
  getProfileDrafts: vi.fn(),
  getProfileDraft: vi.fn(),
  getDeviceWrites: vi.fn(),
}));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  getProfileDrafts,
  getProfileDraft,
  getDeviceWrites,
}));

beforeEach(() => {
  vi.clearAllMocks();
  getProfileDraft.mockResolvedValue(draftDetail());
  getDeviceWrites.mockResolvedValue({ enabled: true, items: [] });
  getProfileDrafts.mockResolvedValue({ items: [draft()] });
});

describe("DraftsPage", () => {
  it("shows the open queue by default", async () => {
    renderWithQueryClient(<DraftsPage />);
    expect(await screen.findByTestId("draft-list")).toBeInTheDocument();
    expect(getProfileDrafts).toHaveBeenCalledWith({ open: true });
  });

  it("can be switched to everything, including what has already been pushed", async () => {
    const user = setupUser();
    renderWithQueryClient(<DraftsPage />);
    await screen.findByTestId("draft-list");

    await user.click(screen.getByRole("button", { name: /Show everything/ }));

    expect(getProfileDrafts).toHaveBeenLastCalledWith({});
  });

  it("says so when writing to the machine is switched off", async () => {
    // The banner is on this page rather than only on Settings because this is
    // where somebody is standing when an approved draft will not push.
    getDeviceWrites.mockResolvedValue({ enabled: false, items: [] });
    renderWithQueryClient(<DraftsPage />);

    const banner = await screen.findByTestId("writes-disabled-banner");
    expect(banner).toHaveTextContent("switched off");
    expect(screen.getByRole("link", { name: /Settings/ })).toHaveAttribute("href", "/settings");
  });

  it("does not nag when writes are on", async () => {
    renderWithQueryClient(<DraftsPage />);
    await screen.findByTestId("draft-list");
    expect(screen.queryByTestId("writes-disabled-banner")).not.toBeInTheDocument();
  });

  it("sends somebody to the profiles page when there is nothing to review", async () => {
    getProfileDrafts.mockResolvedValue({ items: [] });
    renderWithQueryClient(<DraftsPage />);

    expect(await screen.findByText("Nothing waiting")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /profiles page/ })).toHaveAttribute(
      "href",
      "/profiles",
    );
  });
});
