import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { toast } from "sonner";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ImportResult, ImportSummary } from "@/api/types";
import { ImportDropZone } from "@/components/shots/ImportDropZone";
import { ImportResults } from "@/components/shots/ImportResults";
import { renderWithQueryClient, setupUser } from "@/test/renderWithQueryClient";

/**
 * The per-file list that used to be the Import page.
 *
 * A batch never aborts, so "did that work" and "which of those files did not
 * land" are two different questions and only the second one is ever actually
 * asked. These are the cases that make the difference visible: a file the
 * archive already had, and a request that never produced a list at all.
 */

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
    created: count("created"),
    updated: count("updated"),
    skipped: count("skipped"),
    failed: count("failed"),
  };
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("ImportResults", () => {
  it("reports a shot already in the archive as skipped, with the way round it", async () => {
    const user = setupUser();
    renderWithQueryClient(
      <ImportResults
        summary={summary([
          result({
            status: "skipped",
            message: "already in the archive; pass replace to overwrite it",
          }),
        ])}
      />,
    );

    // Skipped is not failed, and the summary line keeps them apart: "0
    // imported · 1 skipped · 0 failed" is the whole answer for this batch.
    const block = screen.getByTestId("import-result");
    expect(block).toHaveTextContent("0 imported");
    expect(block).toHaveTextContent("1 skipped");
    expect(block).toHaveTextContent("0 failed");

    await user.click(within(block).getByTestId("import-result-toggle"));

    expect(within(block).getByText("skipped")).toBeInTheDocument();
    expect(within(block).getByText(/pass replace to overwrite it/)).toBeInTheDocument();
  });

  it("keeps two rows that share a file name apart", () => {
    // A multi-profile export reports one row per profile, so the file name
    // alone is not a key — it renders one row and loses the rest.
    renderWithQueryClient(
      <ImportResults
        summary={summary([
          result({
            filename: "profiles.json",
            kind: "profile",
            status: "created",
            shot_id: null,
            device_id: null,
            profile_version_id: 2,
            label: "Cremina v2",
            message: "profile version 2",
          }),
          result({
            filename: "profiles.json",
            kind: "profile",
            status: "created",
            shot_id: null,
            device_id: null,
            profile_version_id: 3,
            label: "Turbo",
            message: "profile version 3",
          }),
        ])}
      />,
    );

    expect(screen.getByRole("button", { name: "Show files" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Show files" }));

    expect(screen.getByRole("link", { name: "Cremina v2" })).toHaveAttribute(
      "href",
      "/profiles#version-2",
    );
    expect(screen.getByRole("link", { name: "Turbo" })).toHaveAttribute(
      "href",
      "/profiles#version-3",
    );
  });
});

describe("ImportDropZone", () => {
  it("surfaces a request that failed outright rather than showing an empty list", async () => {
    // Nothing was imported, so there is no per-file list to show — the toast
    // carries the reason. The strip stays, so the retry is one click away.
    importFiles.mockRejectedValue(new Error("Upload exceeds the 50 MB limit"));

    renderWithQueryClient(<ImportDropZone />);

    const file = new File(['{"id":"000101"}'], "huge.json", { type: "application/json" });
    fireEvent.drop(screen.getByTestId("shots-dropzone"), { dataTransfer: { files: [file] } });

    await waitFor(() => expect(importFiles).toHaveBeenCalled());
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith("Upload exceeds the 50 MB limit"));
    expect(screen.queryByTestId("import-result")).not.toBeInTheDocument();
    expect(screen.getByTestId("shots-dropzone")).toBeInTheDocument();
  });
});
