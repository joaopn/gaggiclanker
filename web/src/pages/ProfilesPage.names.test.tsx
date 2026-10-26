import { screen, waitFor, within } from "@testing-library/react";
import { toast } from "sonner";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiClientError } from "@/api/client";
import type { BoardRowView } from "@/api/types";
import { RENAME_SENTENCE } from "@/components/profiles/RenameProfile";
import { ProfilesPage } from "@/pages/ProfilesPage";
import {
  boardAction,
  boardRowView,
  boardView,
  conflictView,
  listedVersion,
  profileWith,
  versionsView,
} from "@/test/boardFixtures";
import { boardRowPin } from "@/test/pinFixtures";
import { renderWithQueryClient, setupUser } from "@/test/renderWithQueryClient";
import { signatureNone } from "@/test/signatureFixtures";
import lmleva from "../../../tests/fixtures/profiles/firmware-lmleva.json";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
  Toaster: () => null,
}));
vi.mock("react-chartjs-2", () => ({ Line: () => <canvas /> }));

const api = vi.hoisted(() => ({
  getProfileBoard: vi.fn(),
  getBoardVersions: vi.fn(),
  getBoardConflict: vi.fn(),
  renameBoardProfile: vi.fn(),
  resolveBoardConflict: vi.fn(),
  getSignature: vi.fn(),
}));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  ...api,
}));

beforeEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
  api.getProfileBoard.mockResolvedValue(boardView());
  api.getBoardVersions.mockResolvedValue(
    versionsView([listedVersion({ version_id: 7, is_active: true })]),
  );
  api.getBoardConflict.mockResolvedValue(null);
  api.getSignature.mockResolvedValue(signatureNone);
  api.renameBoardProfile.mockResolvedValue(boardRowView().row);
  api.resolveBoardConflict.mockResolvedValue(boardRowView().row);
});

const board = (...rows: BoardRowView[]) =>
  api.getProfileBoard.mockResolvedValue(boardView({ rows }));

async function openFirst(user: ReturnType<typeof setupUser>) {
  renderWithQueryClient(<ProfilesPage />);
  await user.click(await screen.findByTestId("profile-toggle"));
  return screen.findByTestId("profile-dropdown");
}

describe("Rename, in an open profile", () => {
  it("sits beside the profile's name and opens the Name field with its sentence", async () => {
    const user = setupUser();
    const dropdown = await openFirst(user);

    expect(within(dropdown).getByTestId("profile-name")).toHaveTextContent("9 Bar Espresso");
    await user.click(within(dropdown).getByTestId("rename-open"));

    const form = await screen.findByTestId("rename-form");
    expect(within(form).getByLabelText("Name")).toHaveValue("9 Bar Espresso");
    expect(form).toHaveTextContent(RENAME_SENTENCE);
    expect(RENAME_SENTENCE).toBe(
      "The machine gets the new name at the next sync. Shots keep the name the machine showed when they were brewed.",
    );
    expect(within(form).getByTestId("rename-cancel")).toHaveTextContent("Cancel");
    expect(within(form).getByTestId("rename-submit")).toHaveTextContent("Rename");
    expect(within(form).getByLabelText("Name")).toHaveFocus();
  });

  it("is disabled while the field is empty or unchanged, and a change of case alone is a change", async () => {
    const user = setupUser();
    await openFirst(user);
    await user.click(screen.getByTestId("rename-open"));
    const field = screen.getByLabelText("Name");
    const submit = screen.getByTestId("rename-submit");

    expect(submit).toBeDisabled();
    await user.clear(field);
    expect(submit).toBeDisabled();
    await user.type(field, "   ");
    expect(submit).toBeDisabled();
    await user.clear(field);
    await user.type(field, "9 BAR Espresso");
    expect(submit).toBeEnabled();
    await user.clear(field);
    await user.type(field, "9 Bar Espresso");
    expect(submit).toBeDisabled();
  });

  it("renames with the typed name, trimmed, on the button and on Enter, then closes", async () => {
    const user = setupUser();
    await openFirst(user);
    await user.click(screen.getByTestId("rename-open"));
    await user.clear(screen.getByLabelText("Name"));
    await user.type(screen.getByLabelText("Name"), "  Blooming milk  ");

    await user.click(screen.getByTestId("rename-submit"));

    await waitFor(() => expect(api.renameBoardProfile).toHaveBeenCalledWith(1, "Blooming milk"));
    expect(api.renameBoardProfile).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(screen.queryByTestId("rename-form")).not.toBeInTheDocument());

    await user.click(screen.getByTestId("rename-open"));
    await user.clear(screen.getByLabelText("Name"));
    await user.type(screen.getByLabelText("Name"), "Second{Enter}");
    await waitFor(() => expect(api.renameBoardProfile).toHaveBeenLastCalledWith(1, "Second"));
  });

  it("sends one request however fast Rename is pressed", async () => {
    const user = setupUser();
    let release: (value: unknown) => void = () => {};
    api.renameBoardProfile.mockReturnValue(
      new Promise((resolve) => {
        release = resolve;
      }),
    );
    await openFirst(user);
    await user.click(screen.getByTestId("rename-open"));
    await user.clear(screen.getByLabelText("Name"));
    await user.type(screen.getByLabelText("Name"), "Quick");
    const submit = screen.getByTestId("rename-submit");

    await user.dblClick(submit);
    await user.keyboard("{Enter}");

    expect(api.renameBoardProfile).toHaveBeenCalledTimes(1);
    release(boardRowView().row);
    await waitFor(() => expect(screen.queryByTestId("rename-form")).not.toBeInTheDocument());
  });

  it("cancels with Escape and with Cancel, sending nothing", async () => {
    const user = setupUser();
    await openFirst(user);
    await user.click(screen.getByTestId("rename-open"));
    await user.type(screen.getByLabelText("Name"), "x{Escape}");
    expect(screen.queryByTestId("rename-form")).not.toBeInTheDocument();
    expect(screen.getByTestId("profile-dropdown")).toBeInTheDocument();

    await user.click(screen.getByTestId("rename-open"));
    expect(screen.getByLabelText("Name")).toHaveValue("9 Bar Espresso");
    await user.click(screen.getByTestId("rename-cancel"));
    expect(screen.queryByTestId("rename-form")).not.toBeInTheDocument();
    expect(api.renameBoardProfile).not.toHaveBeenCalled();
  });

  it("shows the server's sentence under the field and keeps what was typed", async () => {
    const user = setupUser();
    api.renameBoardProfile.mockRejectedValue(
      new ApiClientError("There is already a profile called Espresso. Choose another name.", {
        status: 409,
        code: "conflict",
      }),
    );
    await openFirst(user);
    await user.click(screen.getByTestId("rename-open"));
    await user.clear(screen.getByLabelText("Name"));
    await user.type(screen.getByLabelText("Name"), "espresso{Enter}");

    const problem = await screen.findByTestId("rename-problem");
    expect(problem).toHaveTextContent(
      "There is already a profile called Espresso. Choose another name.",
    );
    expect(screen.getByLabelText("Name")).toHaveValue("espresso");
    expect(screen.getByLabelText("Name")).toHaveAttribute("aria-invalid", "true");
    await user.type(screen.getByLabelText("Name"), "2");
    expect(screen.queryByTestId("rename-problem")).not.toBeInTheDocument();
  });

  it("says where to look when no answer came back, not whether it worked", async () => {
    const user = setupUser();
    api.renameBoardProfile.mockRejectedValue(new TypeError("Failed to fetch"));
    await openFirst(user);
    await user.click(screen.getByTestId("rename-open"));
    await user.type(screen.getByLabelText("Name"), "x{Enter}");

    expect(await screen.findByTestId("rename-problem")).toHaveTextContent(
      "No answer came back from the app. Check the profile before trying again.",
    );
  });
});

describe("a pinned row", () => {
  it("has Rename, beside its name, above the frozen sentence", async () => {
    const user = setupUser();
    board(
      boardRowView({
        row: { id: 7, label: "Blooming v1.2" },
        pin: boardRowPin(),
        active_version: {
          version_id: 901,
          short_hash: "c0ffee00",
          label: "Blooming v1.2",
          type: "pro",
          utility: false,
          source: "machine",
          created_at: "2026-10-03T09:00:00Z",
          shots_brewed: 14,
          profile: lmleva as Record<string, unknown>,
        },
      }),
    );
    renderWithQueryClient(<ProfilesPage />);
    await user.click(await screen.findByTestId("profile-toggle"));
    const panel = await screen.findByTestId("pinned-panel");

    expect(within(panel).getByTestId("profile-name")).toHaveTextContent("Blooming v1.2");
    expect(within(panel).getByTestId("pinned-frozen")).toHaveTextContent("Frozen: this copy of");
    await user.click(within(panel).getByTestId("rename-open"));
    await user.clear(screen.getByLabelText("Name"));
    await user.type(screen.getByLabelText("Name"), "My copy{Enter}");
    await waitFor(() => expect(api.renameBoardProfile).toHaveBeenCalledWith(7, "My copy"));
  });
});

describe("a pinned row with a name conflict", () => {
  it("shows the line and its choices, and no side-by-side panel", async () => {
    const user = setupUser();
    board(
      boardRowView({
        row: { id: 7, label: "Blooming v1.2" },
        pin: boardRowPin(),
        in_conflict: true,
        conflict: {
          device_id: "pin7",
          version_id: 901,
          content_hash: "0ddba11",
          short_hash: "0ddba11",
          machine_name: "Blooming v1.2 latte",
          name_only: true,
        },
        active_version: {
          version_id: 901,
          short_hash: "c0ffee00",
          label: "Blooming v1.2",
          type: "pro",
          utility: false,
          source: "machine",
          created_at: "2026-10-03T09:00:00Z",
          shots_brewed: 14,
          profile: lmleva as Record<string, unknown>,
        },
      }),
    );
    // The conflict the server would serve; were the panel mounted it would render this.
    api.getBoardConflict.mockResolvedValue(
      conflictView({
        row_id: 7,
        app_name: "Blooming v1.2",
        machine_name: "Blooming v1.2 latte",
        app_profile: { ...profileWith(9), label: "Blooming v1.2" },
        machine_profile: { ...profileWith(9), label: "Blooming v1.2 latte" },
      }),
    );
    renderWithQueryClient(<ProfilesPage />);
    expect(await screen.findByTestId("profile-state")).toHaveTextContent(
      "Renamed here to Blooming v1.2 and on the machine to Blooming v1.2 latte",
    );
    expect(screen.getByTestId("keep-machine-name")).toHaveTextContent("Keep Blooming v1.2 latte");
    await user.click(screen.getByTestId("profile-toggle"));

    const panel = await screen.findByTestId("pinned-panel");
    expect(within(panel).getByTestId("rename-open")).toBeInTheDocument();
    expect(within(panel).queryByTestId("conflict-panel")).not.toBeInTheDocument();
    expect(within(panel).queryByTestId("conflict-none")).not.toBeInTheDocument();
    expect(api.getBoardConflict).not.toHaveBeenCalled();
  });
});

describe("the row's lines", () => {
  it("after a rename and before the sync: will be renamed on the machine", async () => {
    board(
      boardRowView({
        row: { label: "Blooming milk" },
        planned: [
          boardAction({
            row_id: 1,
            reason: "renamed",
            device_id: "9bar",
            detail: "Becomes Blooming milk v2 on the machine at the next sync",
          }),
        ],
      }),
    );
    renderWithQueryClient(<ProfilesPage />);

    const line = await screen.findByTestId("profile-state");
    expect(line).toHaveTextContent("Becomes Blooming milk v2 on the machine at the next sync");
    expect(line).not.toHaveTextContent("put on the machine");
    expect(line).toHaveAttribute("data-tone", "info");
  });

  it("after a sync that took the machine's name: a note under On the machine", async () => {
    board(
      boardRowView({
        row: { label: "Blooming milk" },
        renamed_on_machine: { from_name: "Blooming", at: "2026-10-12T14:02:00Z" },
      }),
    );
    renderWithQueryClient(<ProfilesPage />);

    const line = await screen.findByTestId("profile-state");
    expect(line).toHaveTextContent("On the machine");
    expect(line).toHaveTextContent("Renamed on the machine from Blooming at the sync of ");
    expect(line.textContent).toMatch(/Oct 12, 2026/);
    expect(line.textContent).toMatch(/\.$/);
  });

  it("has no note on a row that was not renamed", async () => {
    renderWithQueryClient(<ProfilesPage />);
    expect(await screen.findByTestId("profile-state")).not.toHaveTextContent("Renamed");
  });

  describe("a name taken by another profile", () => {
    const taken = () =>
      board(
        boardRowView({
          row: { label: "Blooming" },
          in_conflict: true,
          conflict: {
            device_id: "9bar",
            version_id: 7,
            content_hash: "feedface00",
            short_hash: "feedface",
            machine_name: "Espresso",
            machine_name_held_by: "Espresso",
            name_only: true,
          },
        }),
      );

    it("says so, and offers keeping the app's name or renaming", async () => {
      taken();
      renderWithQueryClient(<ProfilesPage />);

      const line = await screen.findByTestId("profile-state");
      expect(line).toHaveTextContent(
        "Renamed on the machine to Espresso, which another profile is called",
      );
      expect(line).toHaveAttribute("data-tone", "warn");
      const actions = screen.getByTestId("name-conflict-actions");
      expect(within(actions).getByTestId("keep-app-name")).toHaveTextContent(
        "Keep Blooming (rename it back on the machine)",
      );
      expect(within(actions).getByTestId("rename-to")).toHaveTextContent("Rename to …");
      expect(within(actions).queryByTestId("keep-machine-name")).not.toBeInTheDocument();
    });

    it("Keep <name> is the conflict route's app side, with the hash the list saw", async () => {
      const user = setupUser();
      taken();
      renderWithQueryClient(<ProfilesPage />);

      await user.click(await screen.findByTestId("keep-app-name"));

      await waitFor(() =>
        expect(api.resolveBoardConflict).toHaveBeenCalledWith(1, "app", "feedface00"),
      );
      expect(api.resolveBoardConflict).toHaveBeenCalledTimes(1);
    });

    it("Rename to … opens the profile with the Name field, and its Rename sends the rename", async () => {
      const user = setupUser();
      taken();
      renderWithQueryClient(<ProfilesPage />);

      await user.click(await screen.findByTestId("rename-to"));

      const form = await screen.findByTestId("rename-form");
      expect(screen.getByTestId("profile-row")).toHaveAttribute("data-open", "yes");
      expect(within(form).getByLabelText("Name")).toHaveFocus();
      await user.clear(within(form).getByLabelText("Name"));
      await user.type(within(form).getByLabelText("Name"), "Blooming latte{Enter}");
      await waitFor(() => expect(api.renameBoardProfile).toHaveBeenCalledWith(1, "Blooming latte"));
      expect(api.resolveBoardConflict).not.toHaveBeenCalled();
    });

    it("opens no side-by-side panel: the line is the whole question", async () => {
      const user = setupUser();
      taken();
      // The conflict the server would serve for it: were the panel mounted it would render this.
      api.getBoardConflict.mockResolvedValue(
        conflictView({
          app_name: "Blooming",
          machine_name: "Espresso",
          machine_name_held_by: "Espresso",
          app_profile: { ...profileWith(9), label: "Blooming" },
          machine_profile: { ...profileWith(9), label: "Espresso" },
        }),
      );
      const dropdown = await openFirst(user);
      expect(within(dropdown).getByTestId("rename-open")).toBeInTheDocument();
      expect(within(dropdown).queryByTestId("conflict-panel")).not.toBeInTheDocument();
      expect(within(dropdown).queryByTestId("conflict-none")).not.toBeInTheDocument();
      expect(api.getBoardConflict).not.toHaveBeenCalled();
    });

    it("keeping a name speaks of the name, not of a version", async () => {
      const user = setupUser();
      taken();
      renderWithQueryClient(<ProfilesPage />);
      await user.click(await screen.findByTestId("keep-app-name"));
      await waitFor(() => expect(toast.success).toHaveBeenCalled());
      expect(vi.mocked(toast.success).mock.calls[0]?.[0]).toBe("9 Bar Espresso keeps its name");
      expect(JSON.stringify(vi.mocked(toast.success).mock.calls[0])).not.toMatch(/version/);
    });
  });

  describe("renamed on both sides", () => {
    const both = () =>
      board(
        boardRowView({
          row: { label: "Blooming milk" },
          in_conflict: true,
          conflict: {
            device_id: "9bar",
            version_id: 7,
            content_hash: "0ddba11",
            short_hash: "0ddba11",
            machine_name: "Blooming latte",
            name_only: true,
          },
        }),
      );

    it("says both names and offers each", async () => {
      both();
      renderWithQueryClient(<ProfilesPage />);

      expect(await screen.findByTestId("profile-state")).toHaveTextContent(
        "Renamed here to Blooming milk and on the machine to Blooming latte",
      );
      expect(screen.getByTestId("keep-app-name")).toHaveTextContent(/^Keep Blooming milk$/);
      expect(screen.getByTestId("keep-machine-name")).toHaveTextContent(/^Keep Blooming latte$/);
      expect(screen.queryByTestId("rename-to")).not.toBeInTheDocument();
    });

    it("keeping the machine's name says so in the toast", async () => {
      const user = setupUser();
      both();
      renderWithQueryClient(<ProfilesPage />);
      await user.click(await screen.findByTestId("keep-machine-name"));
      await waitFor(() => expect(toast.success).toHaveBeenCalled());
      expect(vi.mocked(toast.success).mock.calls[0]?.[0]).toBe(
        "9 Bar Espresso keeps the name the machine gave it",
      );
      expect(JSON.stringify(vi.mocked(toast.success).mock.calls[0])).not.toMatch(/version/);
    });

    it("each button is the conflict route's side it names", async () => {
      const user = setupUser();
      both();
      renderWithQueryClient(<ProfilesPage />);

      await user.click(await screen.findByTestId("keep-machine-name"));
      await waitFor(() =>
        expect(api.resolveBoardConflict).toHaveBeenLastCalledWith(1, "machine", "0ddba11"),
      );
      await user.click(screen.getByTestId("keep-app-name"));
      await waitFor(() =>
        expect(api.resolveBoardConflict).toHaveBeenLastCalledWith(1, "app", "0ddba11"),
      );
    });
  });

  describe("a conflict about the brew", () => {
    it("keeps today's panel and today's line, and shows the name on each side when both differ", async () => {
      const user = setupUser();
      board(
        boardRowView({
          row: { label: "Blooming" },
          in_conflict: true,
          conflict: {
            device_id: "9bar",
            version_id: 31,
            content_hash: "feedface00",
            short_hash: "feedface",
            machine_name: "Blooming latte",
            name_only: false,
          },
        }),
      );
      api.getBoardConflict.mockResolvedValue(
        conflictView({
          app_name: "Blooming",
          machine_name: "Blooming latte",
          app_profile: { ...profileWith(9), label: "Blooming" },
          machine_profile: { ...profileWith(7), label: "Blooming latte" },
        }),
      );
      const dropdown = await openFirst(user);

      expect(screen.getByTestId("profile-state")).toHaveTextContent(
        "Edited outside the app: choose a side",
      );
      expect(screen.queryByTestId("name-conflict-actions")).not.toBeInTheDocument();
      const panel = await within(dropdown).findByTestId("conflict-panel");
      const app = within(panel).getByTestId("conflict-app");
      const machine = within(panel).getByTestId("conflict-machine");
      const named = (side: HTMLElement) =>
        within(side)
          .getAllByTestId("conflict-difference")
          .map((el) => el.textContent)
          .find((text) => text?.startsWith("name"));
      expect(named(app)).toBe("name Blooming");
      expect(named(machine)).toBe("name Blooming latte");
    });
  });
});
