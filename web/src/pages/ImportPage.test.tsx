import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ImportResult, ImportSummary } from "@/api/types";
import { ImportPage } from "@/pages/ImportPage";
import { renderWithQueryClient } from "@/test/renderWithQueryClient";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
  Toaster: () => null,
}));

const { importFiles } = vi.hoisted(() => ({ importFiles: vi.fn() }));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  importFiles,
}));

/** Shaped exactly like `ImportResult` in gaggiclanker/imports/service.py. */
function result(overrides: Partial<ImportResult> = {}): ImportResult {
  return {
    filename: "shot-129.json",
    kind: "shot",
    status: "created",
    message: "213 samples",
    shot_id: 4,
    device_id: "000129",
    profile_version_id: null,
    label: null,
    quarantined: false,
    ...overrides,
  };
}

function summary(items: ImportResult[]): ImportSummary {
  const count = (status: string) => items.filter((item) => item.status === status).length;
  return {
    items,
    machine_id: 1,
    created: count("created"),
    updated: count("updated"),
    skipped: count("skipped"),
    failed: count("failed"),
  };
}

function jsonFile(name = "shot-129.json") {
  return new File(['{"samples": []}'], name, { type: "application/json" });
}

async function pick(files: File[]) {
  const user = userEvent.setup();
  await user.upload(screen.getByTestId("import-input"), files);
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("ImportPage", () => {
  it("says what to do before anything has been imported", () => {
    renderWithQueryClient(<ImportPage />);

    expect(screen.getByText("Nothing imported yet")).toBeInTheDocument();
    expect(screen.getByTestId("import-dropzone")).toBeInTheDocument();
  });

  it("uploads the chosen files and lists a row for each", async () => {
    importFiles.mockResolvedValue(
      summary([
        result(),
        result({
          filename: "profile-dCs4AOOcBn.json",
          kind: "profile",
          shot_id: null,
          device_id: null,
          profile_version_id: 2,
          label: "Cremina v2",
          message: "profile version 2",
        }),
      ]),
    );

    renderWithQueryClient(<ImportPage />);
    await pick([jsonFile(), jsonFile("profile-dCs4AOOcBn.json")]);

    expect(await screen.findByText("213 samples")).toBeInTheDocument();
    expect(screen.getByText("shot 000129")).toHaveAttribute("href", "/shots");
    expect(screen.getByText("Cremina v2")).toHaveAttribute("href", "/profiles");
    expect(importFiles).toHaveBeenCalledWith(expect.arrayContaining([expect.any(File)]), {
      replace: false,
    });
  });

  it("shows a failed file beside the ones that worked", async () => {
    // The whole point of per-file results: a batch does not abort, so the page
    // has to show which file did not land and why.
    importFiles.mockResolvedValue(
      summary([
        result({ filename: "broken.json", status: "failed", message: "not JSON", shot_id: null }),
        result(),
      ]),
    );

    renderWithQueryClient(<ImportPage />);
    await pick([jsonFile("broken.json"), jsonFile()]);

    expect(await screen.findByText("failed")).toBeInTheDocument();
    expect(screen.getByText("not JSON")).toBeInTheDocument();
    expect(screen.getByText("213 samples")).toBeInTheDocument();
  });

  it("reports a shot already in the archive as skipped", async () => {
    importFiles.mockResolvedValue(
      summary([
        result({
          status: "skipped",
          message: "already in the archive; pass replace to overwrite it",
        }),
      ]),
    );

    renderWithQueryClient(<ImportPage />);
    await pick([jsonFile()]);

    expect(await screen.findByText("skipped")).toBeInTheDocument();
    expect(screen.getByText(/pass replace to overwrite it/)).toBeInTheDocument();
  });

  it("sends the replace flag when it is ticked", async () => {
    importFiles.mockResolvedValue(summary([result({ status: "updated" })]));
    const user = userEvent.setup();

    renderWithQueryClient(<ImportPage />);
    await user.click(screen.getByLabelText("Replace shots already in the archive"));
    await pick([jsonFile()]);

    await waitFor(() => {
      expect(importFiles).toHaveBeenCalledWith(expect.anything(), { replace: true });
    });
  });

  it("surfaces a request that failed outright", async () => {
    importFiles.mockRejectedValue(new Error("Upload exceeds the 50 MB limit"));

    renderWithQueryClient(<ImportPage />);
    await pick([jsonFile()]);

    // No result table: nothing was imported, and the toast (mocked) carries the
    // message. The dropzone stays, so the retry is one click away.
    await waitFor(() => expect(importFiles).toHaveBeenCalled());
    expect(screen.getByTestId("import-dropzone")).toBeInTheDocument();
    expect(screen.queryByText("Results")).not.toBeInTheDocument();
  });
});
