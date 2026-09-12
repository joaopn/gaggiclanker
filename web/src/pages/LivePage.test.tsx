import { act, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { LiveStatus, ShotListData } from "@/api/types";
import { publishLiveConnection, publishLiveStatus, resetLiveStatus } from "@/lib/liveStatus";
import { LivePage } from "@/pages/LivePage";
import { renderWithQueryClient } from "@/test/renderWithQueryClient";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
  Toaster: () => null,
}));

const { getShots } = vi.hoisted(() => ({ getShots: vi.fn() }));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  getShots,
}));

/**
 * The fake device's own brew sequence, in miniature.
 *
 * `gaggiclanker/device/fake.py --brew-every` pushes exactly this shape: a
 * `process` object with `a: 1`, a rising `e`, and a volumetric `pt`/`pp` pair,
 * followed by one frame with `a: 0`.
 */
function brewFrame(elapsedMs: number, weight: number): LiveStatus {
  return {
    process: {
      a: 1,
      s: "brew",
      l: elapsedMs < 4000 ? "Fill" : "Ramp",
      e: elapsedMs,
      tt: "volumetric",
      pt: 36,
      pp: weight,
    },
    ct: 92.6,
    tt: 93,
    pr: 6.4,
    fl: 2.1,
    cw: weight,
  };
}

/** The store rate-limits to 2 Hz, so a burst needs time between frames. */
async function push(status: LiveStatus, advanceMs = 600) {
  await act(async () => {
    publishLiveStatus(status);
    await new Promise((resolve) => setTimeout(resolve, advanceMs));
  });
}

function shotRow(id: number, label: string): ShotListData["items"][number] {
  return {
    id,
    device_id: String(id).padStart(6, "0"),
    machine_id: 1,
    source: "device",
    started_at: "2026-03-04T08:15:00.000Z",
    start_epoch: 1_772_611_200,
    duration_ms: 28_000,
    profile_version_id: null,
    profile_id_on_device: "9bar",
    profile_name_on_device: label,
    profile_label: null,
    final_weight_g: 36,
    volume_g: 36,
    index_rating: null,
    index_avg_temp_c: null,
    index_max_pressure_bar: null,
    index_avg_flow_ml_s: null,
    execution_score: 8,
    execution_reason: "",
    sample_count: 112,
    scale_connected: true,
    incomplete: false,
    quarantined: false,
    quarantine_reason: null,
    deleted_on_device: false,
    rating: null,
    has_notes: false,
    has_judgement: false,
    set_version_id: null,
    set_badge: null,
    synced_at: "2026-03-04T08:15:30.000Z",
  };
}

const emptyList: ShotListData = {
  items: [],
  total: 0,
  limit: 1,
  offset: null,
  next_cursor: null,
};

beforeEach(() => {
  vi.clearAllMocks();
  resetLiveStatus();
  getShots.mockResolvedValue(emptyList);
});

describe("LivePage", () => {
  it("waits quietly when nothing is brewing", async () => {
    publishLiveConnection({ connected: true, configured: true });

    renderWithQueryClient(<LivePage />);

    expect(await screen.findByText("No shot running")).toBeInTheDocument();
  });

  it("points at Settings when no machine is configured", async () => {
    publishLiveConnection({ connected: false, configured: false });

    renderWithQueryClient(<LivePage />);

    expect(await screen.findByText("No machine configured")).toBeInTheDocument();
  });

  it("follows a brew from the fake device's frames", async () => {
    publishLiveConnection({ connected: true, configured: true });
    renderWithQueryClient(<LivePage />);

    await push(brewFrame(500, 0.9));

    const readout = await screen.findByTestId("live-readout");
    expect(readout).toHaveTextContent("0:00");
    expect(readout).toHaveTextContent("6.4 bar");
    expect(readout).toHaveTextContent("2.1 ml/s");
    expect(readout).toHaveTextContent("0.9 g");
    expect(readout).toHaveTextContent("92.6 °C");
    expect(screen.getByText(/Fill/)).toBeInTheDocument();

    await push(brewFrame(8000, 18));

    expect(screen.getByTestId("live-readout")).toHaveTextContent("0:08");
    expect(screen.getByText(/Ramp/)).toBeInTheDocument();
  });

  it("reads the target progress in the unit the firmware names", async () => {
    // `pt`/`pp` are grams when `tt` is "volumetric" and milliseconds when it is
    // "time". A bar labelled "18 / 36" means nothing without that.
    publishLiveConnection({ connected: true, configured: true });
    renderWithQueryClient(<LivePage />);

    await push(brewFrame(8000, 18));

    const progress = await screen.findByTestId("target-progress");
    expect(progress).toHaveTextContent("Volumetric target");
    expect(progress).toHaveTextContent("18.0 / 36.0 g");
  });

  it("starts a fresh curve for a second brew instead of joining it to the first", async () => {
    // Two shots in a row is the normal case on a busy morning. The frames
    // between them carry `a: 0`, and without clearing there the new shot's
    // first sample is drawn with a line back to the end of the previous one.
    publishLiveConnection({ connected: true, configured: true });
    renderWithQueryClient(<LivePage />);

    await push(brewFrame(500, 0.9));
    await push(brewFrame(8000, 18));
    await push(brewFrame(20_000, 34));
    let series = await screen.findByTestId("live-series");
    expect(series).toHaveTextContent("Pressure: 3 points, 0.5s to 20.0s");

    await push({ process: { a: 0, s: "brew", l: "Finished", e: 28_000 }, cw: 36 });
    await push(brewFrame(500, 0.8));

    series = await screen.findByTestId("live-series");
    expect(series).toHaveTextContent("Pressure: 1 points, 0.5s to 0.5s");
  });

  it("waits for the file rather than linking to the shot before it", async () => {
    // For a second or two after a brew ends the archive's newest row is still
    // the *previous* shot, and that is exactly when somebody is looking.
    getShots.mockResolvedValue({ ...emptyList, total: 1, items: [shotRow(41, "Yesterday")] });
    publishLiveConnection({ connected: true, configured: true });
    renderWithQueryClient(<LivePage />);

    await push(brewFrame(8000, 18));
    await push({ process: { a: 0, s: "brew", l: "Finished", e: 28_000 }, cw: 36 });

    expect(await screen.findByText("Waiting for the file")).toBeInTheDocument();
    expect(screen.queryByTestId("saved-shot-link")).not.toBeInTheDocument();
  });

  it("links to the saved shot once the archive has a row it did not have", async () => {
    // The brew ends, the sync engine pulls the .slog, `shot.ingested`
    // invalidates the list, and the newest row is one the page has not seen.
    getShots.mockResolvedValue({ ...emptyList, total: 1, items: [shotRow(41, "Yesterday")] });
    publishLiveConnection({ connected: true, configured: true });
    const { queryClient } = renderWithQueryClient(<LivePage />);

    await push(brewFrame(8000, 18));
    await push({ process: { a: 0, s: "brew", l: "Finished", e: 28_000 }, cw: 36 });
    await screen.findByText("Waiting for the file");

    getShots.mockResolvedValue({
      ...emptyList,
      total: 2,
      items: [shotRow(42, "Simulated brew")],
    });
    await act(async () => {
      await queryClient.invalidateQueries();
    });

    await waitFor(() =>
      expect(screen.getByTestId("saved-shot-link")).toHaveAttribute("href", "/shots/42"),
    );
  });
});
