import { act, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { RestoreCheckData } from "@/api/types";
import { renderWithQueryClient, setupUser } from "@/test/renderWithQueryClient";

const { checkRestore, cancelRestore, applyRestore, getHealth } = vi.hoisted(() => ({
  checkRestore: vi.fn(),
  cancelRestore: vi.fn(),
  applyRestore: vi.fn(),
  getHealth: vi.fn(),
}));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  checkRestore,
  cancelRestore,
  applyRestore,
  getHealth,
}));

import { ApiClientError } from "@/api/client";
import {
  RESTART_GIVE_UP_MS,
  RestartOverlay,
  RestoreSection,
  refusalText,
} from "@/pages/settings/RestoreSection";

const CHECK: RestoreCheckData = {
  token: "a".repeat(32),
  filename: "gaggiclanker-20261001T101500Z.db",
  size_bytes: 1234,
  manifest: {
    format_version: 1,
    app_version: "0.1.0",
    schema_version: "3f9a1c2b7d40",
    created_at: "2026-10-01T10:15:00Z",
    keys_included: false,
  },
  in_file: { shots: 412, sets: 6, beans: 9 },
  now: { shots: 431, sets: 7, beans: 9 },
  keys_in_file: false,
  keys_here: true,
};

function file(name = "gaggiclanker-20261001T101500Z.db") {
  return new File(["SQLite format 3"], name, { type: "application/octet-stream" });
}

async function choose(user: ReturnType<typeof setupUser>, f = file()) {
  await user.upload(screen.getByTestId("restore-file"), f);
}

describe("RestoreSection", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("offers only the upload at first", () => {
    renderWithQueryClient(<RestoreSection />);
    expect(
      screen.getByText("Replaces everything in the app with what the file holds."),
    ).toBeVisible();
    expect(screen.getByRole("button", { name: "Restore from file…" })).toBeVisible();
  });

  it("uploads, says it is checking, then shows what the file holds beside what is there now", async () => {
    const user = setupUser();
    let release: (v: RestoreCheckData) => void = () => {};
    checkRestore.mockReturnValue(new Promise<RestoreCheckData>((resolve) => (release = resolve)));
    renderWithQueryClient(<RestoreSection />);

    await choose(user);
    expect(await screen.findByText("Checking gaggiclanker-20261001T101500Z.db…")).toBeVisible();
    expect(checkRestore).toHaveBeenCalledTimes(1);

    await act(async () => release(CHECK));
    expect(await screen.findByText("Restore gaggiclanker-20261001T101500Z.db?")).toBeVisible();
    expect(screen.getByText(/Taken .* by gaggiclanker 0\.1\.0/)).toBeVisible();
    const row = (label: string) => screen.getByText(label).closest("tr") as HTMLElement;
    expect(row("Shots")).toHaveTextContent("412");
    expect(row("Shots")).toHaveTextContent("431");
    expect(row("Sets")).toHaveTextContent("67");
    expect(row("Beans")).toHaveTextContent("99");
    const bullets = screen.getAllByRole("listitem").map((item) => item.textContent);
    expect(bullets).toEqual([
      "Everything in the app is replaced by the file.",
      "What the app holds now is gone, unless you have downloaded a backup of it.",
      "Writes to the machine are switched off; switch them on again when you are ready.",
      "Everyone is signed out.",
      "The app restarts, which takes a few seconds.",
    ]);
    expect(screen.queryByText(/pre-restore/)).not.toBeInTheDocument();
    // Nothing has been applied by looking.
    expect(applyRestore).not.toHaveBeenCalled();
  });

  it("says so when the file has no manifest", async () => {
    const user = setupUser();
    checkRestore.mockResolvedValue({ ...CHECK, manifest: null });
    renderWithQueryClient(<RestoreSection />);
    await choose(user);
    expect(
      await screen.findByText("A plain copy of a gaggiclanker database (no backup details)"),
    ).toBeVisible();
  });

  it.each([
    [{ keys_in_file: true, keys_here: true }, "included", "set"],
    [{ keys_in_file: false, keys_here: true }, "not included, yours are kept", "set"],
    [{ keys_in_file: false, keys_here: false }, "not included", "none"],
  ])("words the keys row for %j", async (flags, inFile, now) => {
    const user = setupUser();
    checkRestore.mockResolvedValue({ ...CHECK, ...flags });
    renderWithQueryClient(<RestoreSection />);
    await choose(user);
    const row = (await screen.findByText("API keys and tokens")).closest("tr") as HTMLElement;
    const cells = row.querySelectorAll("td");
    expect(cells[1]).toHaveTextContent(new RegExp(`^${inFile}$`));
    expect(cells[2]).toHaveTextContent(new RegExp(`^${now}$`));
  });

  it("Cancel deletes the staged file and goes back to the start", async () => {
    const user = setupUser();
    checkRestore.mockResolvedValue(CHECK);
    cancelRestore.mockResolvedValue(undefined);
    renderWithQueryClient(<RestoreSection />);
    await choose(user);

    await user.click(await screen.findByRole("button", { name: "Cancel" }));

    await waitFor(() => expect(cancelRestore).toHaveBeenCalledWith(CHECK.token));
    expect(await screen.findByRole("button", { name: "Restore from file…" })).toBeVisible();
    expect(applyRestore).not.toHaveBeenCalled();
  });

  it.each([
    ["RESTORE_NOT_A_DATABASE", /not a gaggiclanker database/],
    ["RESTORE_DAMAGED", /damaged/],
    ["RESTORE_SCHEMA_DIFFERS", /different version of gaggiclanker.*Restore it with the version/],
    ["PAYLOAD_TOO_LARGE", /larger than the 1 GB limit/],
    ["INSUFFICIENT_STORAGE", /not enough free disk space/],
  ])("refuses %s in plain words and offers another file", async (code, words) => {
    const user = setupUser();
    checkRestore.mockRejectedValue(new ApiClientError("server text", { code, status: 422 }));
    renderWithQueryClient(<RestoreSection />);

    await choose(user);

    expect(await screen.findByRole("alert")).toHaveTextContent(words);
    expect(screen.getByRole("button", { name: "Choose another file" })).toBeVisible();
    expect(screen.queryByRole("button", { name: "Restore and restart" })).not.toBeInTheDocument();
  });

  it("tells the person to wait when something is running, on apply", async () => {
    const user = setupUser();
    checkRestore.mockResolvedValue(CHECK);
    applyRestore.mockRejectedValue(
      new ApiClientError("busy", { code: "RESTORE_BUSY", status: 409 }),
    );
    renderWithQueryClient(<RestoreSection />);
    await choose(user);

    await user.click(await screen.findByRole("button", { name: "Restore and restart" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "A sync, a chat answer or a review is running. Try again when it finishes.",
    );
    expect(screen.getByRole("button", { name: "Choose another file" })).toBeVisible();
  });

  it("confirming applies once and covers the page with the restart overlay", async () => {
    const user = setupUser();
    checkRestore.mockResolvedValue(CHECK);
    applyRestore.mockResolvedValue({ restarting: true });
    getHealth.mockResolvedValue({ status: "ok", version: "0.1.0", database: "ok" });
    renderWithQueryClient(<RestoreSection />);
    await choose(user);

    const button = await screen.findByRole("button", { name: "Restore and restart" });
    await user.dblClick(button);

    expect(await screen.findByText("Restoring… the app is restarting.")).toBeVisible();
    expect(screen.getByText("This page reloads by itself.")).toBeVisible();
    expect(applyRestore).toHaveBeenCalledTimes(1);
    expect(applyRestore).toHaveBeenCalledWith(CHECK.token);
  });

  it("sends the file name and bytes to the client", async () => {
    const user = setupUser();
    checkRestore.mockResolvedValue(CHECK);
    renderWithQueryClient(<RestoreSection />);
    await choose(user, file("my backup.db"));
    await screen.findByText(/Restore .*\?/);
    expect(checkRestore.mock.calls[0][0].name).toBe("my backup.db");
  });
});

describe("refusalText", () => {
  it("says a reset is under way in its own words", () => {
    expect(refusalText(new ApiClientError("x", { code: "RESET_PENDING", status: 409 }))).toBe(
      "A reset is under way; the app is about to restart.",
    );
  });

  it("falls back to the server's own words", () => {
    expect(refusalText(new Error("Something odd"))).toBe(
      "This file can't be restored: Something odd",
    );
  });
});

describe("RestartOverlay", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.useFakeTimers();
  });

  it("waits for the app to go down and come back before it reloads", async () => {
    const reload = vi.fn();
    const ok = { status: "ok", version: "0.1.0", database: "ok" };
    // Still answering for a moment after the reply, then gone, then back.
    getHealth
      .mockResolvedValueOnce(ok)
      .mockResolvedValueOnce(ok)
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockResolvedValue(ok);
    renderWithQueryClient(<RestartOverlay reload={reload} />);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_000);
    });
    expect(reload).not.toHaveBeenCalled();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_000);
    });
    expect(reload).not.toHaveBeenCalled();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000);
    });
    expect(reload).toHaveBeenCalledTimes(1);
    vi.useRealTimers();
  });

  it("never reloads while the app only ever answers (it did not go down)", async () => {
    const reload = vi.fn();
    getHealth.mockResolvedValue({ status: "ok", version: "0.1.0", database: "ok" });
    renderWithQueryClient(<RestartOverlay reload={reload} />);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });

    expect(reload).not.toHaveBeenCalled();
    vi.useRealTimers();
  });

  it("says to start it by hand after a minute", async () => {
    const reload = vi.fn();
    getHealth.mockRejectedValue(new TypeError("Failed to fetch"));
    renderWithQueryClient(<RestartOverlay reload={reload} />);
    expect(screen.getByText("Restoring… the app is restarting.")).toBeVisible();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(RESTART_GIVE_UP_MS + 1);
    });

    expect(screen.getByText(/The app has not come back\./)).toBeVisible();
    expect(screen.getByRole("alert")).toHaveTextContent(
      "The app has not come back. If it does not run under Docker (restart: unless-stopped), start it again by hand.",
    );
    expect(screen.queryByText(/restore is done either way/)).not.toBeInTheDocument();
    expect(reload).not.toHaveBeenCalled();
    vi.useRealTimers();
  });
});
