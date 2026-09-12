import { screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { VersionTimeline } from "@/components/sets/VersionTimeline";
import { renderWithQueryClient } from "@/test/renderWithQueryClient";
import { judgement, setDetail, vocabulary } from "@/test/setsFixtures";

const { getVocabulary } = vi.hoisted(() => ({ getVocabulary: vi.fn() }));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  getVocabulary,
}));

beforeEach(() => {
  vi.clearAllMocks();
  getVocabulary.mockResolvedValue(vocabulary);
});

describe("VersionTimeline", () => {
  it("reads newest first and shows the diff against the parent", () => {
    const detail = setDetail();
    renderWithQueryClient(
      <VersionTimeline versions={detail.versions} judgements={detail.judgements} />,
    );

    const entries = screen.getAllByTestId("version-entry");
    expect(entries.map((entry) => entry.dataset.version)).toEqual(["2", "1"]);

    // The diff is the point of the page: a version on its own is a list of
    // numbers.
    const changes = screen.getByTestId("version-changes");
    expect(changes).toHaveTextContent("Grind");
    expect(changes).toHaveTextContent("22");
    expect(changes).toHaveTextContent("21");
    expect(screen.getByTestId("version-intent")).toHaveTextContent(
      "one click finer, chasing the sourness out",
    );
  });

  it("calls version one a starting point rather than six changes", () => {
    const detail = setDetail();
    renderWithQueryClient(
      <VersionTimeline versions={detail.versions} judgements={detail.judgements} />,
    );

    expect(screen.getByTestId("version-baseline")).toHaveTextContent("The starting point.");
  });

  it("says where a version came from, in the server's own words", async () => {
    const detail = setDetail();
    detail.versions[0].version.origin = "analysis";
    renderWithQueryClient(
      <VersionTimeline versions={detail.versions} judgements={detail.judgements} />,
    );

    // The label comes from /api/vocab like every other closed vocabulary here;
    // the slug is what shows while that is in flight.
    expect(screen.getByText("analysis")).toBeInTheDocument();
    expect(await screen.findByText("From an analysis")).toBeInTheDocument();
  });

  it("says a field was cleared rather than drawing an em dash", async () => {
    const detail = setDetail();
    detail.versions[0].changes = [
      { field: "target_temperature_c", label: "Temperature", before: "93 °C", after: null },
      { field: "dose_g", label: "Dose", before: null, after: "18 g" },
    ];
    renderWithQueryClient(
      <VersionTimeline versions={detail.versions} judgements={detail.judgements} />,
    );

    // "93 °C → —" reads as a rendering bug; unsetting a field is a deliberate
    // change and the word is what makes it one.
    const changes = await screen.findByTestId("version-changes");
    expect(changes).toHaveTextContent("93 °C");
    expect(changes).toHaveTextContent("cleared");
    expect(changes).toHaveTextContent("not set");
  });

  it("lists the shots under the version they were pulled with", () => {
    const detail = setDetail();
    detail.versions[0].shots = [
      {
        id: 41,
        device_id: "000141",
        machine_id: 1,
        source: "device",
        started_at: "2026-04-03T08:15:00.000Z",
        start_epoch: 1_775_000_000,
        duration_ms: 28_000,
        profile_version_id: 7,
        profile_id_on_device: "9bar",
        profile_name_on_device: "9 Bar Espresso",
        profile_label: "9 Bar Espresso",
        final_weight_g: 36,
        volume_g: 36,
        index_rating: null,
        index_avg_temp_c: null,
        index_max_pressure_bar: null,
        index_avg_flow_ml_s: null,
        execution_score: 8.4,
        execution_reason: "",
        sample_count: 118,
        scale_connected: true,
        incomplete: false,
        quarantined: false,
        quarantine_reason: null,
        deleted_on_device: false,
        analysis_state: "none",
        rating: null,
        has_notes: false,
        has_judgement: true,
        set_version_id: 22,
        set_badge: { set_id: 3, set_name: "Guji on the Niche", version_no: 2 },
        synced_at: "2026-04-03T08:16:00.000Z",
      },
    ];
    // The judgement's rating wins over the device's: the archive's copy is the
    // one the user edits.
    detail.judgements = { "41": judgement({ shot_id: 41, rating: 5 }) };

    renderWithQueryClient(
      <VersionTimeline versions={detail.versions} judgements={detail.judgements} />,
    );

    const shots = screen.getByTestId("version-shots");
    expect(shots).toHaveTextContent("9 Bar Espresso");
    expect(screen.getByTestId("rating-stars")).toHaveAttribute("data-rating", "5");
  });
});
