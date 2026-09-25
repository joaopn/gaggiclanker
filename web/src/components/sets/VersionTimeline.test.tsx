import { screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { SetVersionDetail } from "@/api/types";
import { VersionTimeline } from "@/components/sets/VersionTimeline";
import { renderWithQueryClient, setupUser } from "@/test/renderWithQueryClient";
import {
  emptyVersion,
  evidence,
  judgement,
  labelCounts,
  setDetail,
  version,
  vocabulary,
} from "@/test/setsFixtures";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
  Toaster: () => null,
}));

const { getVocabulary, setVersionPrediction, setVersionOutcome, clearVersionOutcome, rollbackSet } =
  vi.hoisted(() => ({
    getVocabulary: vi.fn(),
    setVersionPrediction: vi.fn(),
    setVersionOutcome: vi.fn(),
    clearVersionOutcome: vi.fn(),
    rollbackSet: vi.fn(),
  }));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  getVocabulary,
  setVersionPrediction,
  setVersionOutcome,
  clearVersionOutcome,
  rollbackSet,
}));

beforeEach(() => {
  vi.clearAllMocks();
  getVocabulary.mockResolvedValue(vocabulary);
  setVersionPrediction.mockResolvedValue(version());
  setVersionOutcome.mockResolvedValue(version());
  clearVersionOutcome.mockResolvedValue(version());
  rollbackSet.mockResolvedValue(version({ version_no: 3, restores_version_no: 1 }));
});

describe("VersionTimeline", () => {
  it("reads newest first and shows the diff against the parent", () => {
    const detail = setDetail();
    renderWithQueryClient(
      <VersionTimeline setId={3} versions={detail.versions} judgements={detail.judgements} />,
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

  /** The newest entry, as a version that predicted something. */
  function predicting(overrides: Partial<SetVersionDetail> = {}) {
    const detail = setDetail();
    detail.versions[0] = {
      ...detail.versions[0],
      version: version({
        ...detail.versions[0].version,
        prediction: "less bitter, a shorter shot",
        outcome_state: "open",
      }),
      evidence: evidence(),
      ...overrides,
    };
    return detail;
  }

  it("offers the evidence only where there is a prediction to be evidence for", () => {
    const detail = predicting();
    renderWithQueryClient(
      <VersionTimeline setId={3} versions={detail.versions} judgements={detail.judgements} />,
    );

    const disclosures = screen.getAllByTestId("version-evidence");
    expect(disclosures).toHaveLength(1);
    expect(disclosures[0].dataset.version).toBe("2");
  });

  it("opens the evidence of a prediction nobody has graded yet", () => {
    const detail = predicting();
    renderWithQueryClient(
      <VersionTimeline setId={3} versions={detail.versions} judgements={detail.judgements} />,
    );

    // The one entry in the log that is still a live question.
    expect(screen.getByRole("button", { name: "Evidence" })).toHaveAttribute(
      "aria-expanded",
      "true",
    );
    expect(screen.getByTestId("evidence-table")).toBeInTheDocument();
  });

  it("leaves the evidence closed once the prediction has been graded", () => {
    const detail = predicting();
    detail.versions[0] = {
      ...detail.versions[0],
      version: version({
        ...detail.versions[0].version,
        outcome: "held",
        outcome_state: "held",
      }),
    };
    renderWithQueryClient(
      <VersionTimeline setId={3} versions={detail.versions} judgements={detail.judgements} />,
    );

    expect(screen.getByRole("button", { name: "Evidence" })).toHaveAttribute(
      "aria-expanded",
      "false",
    );
  });

  it("leaves the evidence closed on an open prediction with nothing to grade yet", () => {
    const counts = evidence();
    const detail = predicting({
      evidence: evidence({ this: { ...counts.this, shots: 0 } }),
    });
    renderWithQueryClient(
      <VersionTimeline setId={3} versions={detail.versions} judgements={detail.judgements} />,
    );

    expect(screen.getByRole("button", { name: "Evidence" })).toHaveAttribute(
      "aria-expanded",
      "false",
    );
  });

  it("calls version one a starting point rather than six changes", () => {
    const detail = setDetail();
    renderWithQueryClient(
      <VersionTimeline setId={3} versions={detail.versions} judgements={detail.judgements} />,
    );

    expect(screen.getByTestId("version-baseline")).toHaveTextContent("The starting point.");
  });

  it("says where a version came from, in the server's own words", async () => {
    const detail = setDetail();
    detail.versions[0].version.origin = "analysis";
    renderWithQueryClient(
      <VersionTimeline setId={3} versions={detail.versions} judgements={detail.judgements} />,
    );

    // The label comes from /api/vocab like every other closed vocabulary here;
    // the slug is what shows while that is in flight.
    expect(screen.getByText("analysis")).toBeInTheDocument();
    expect(await screen.findByText("From an analysis")).toBeInTheDocument();
  });

  it("says a field was cleared rather than drawing an em dash", async () => {
    const detail = setDetail();
    detail.versions[0].changes = [
      {
        field: "target_yield_g",
        label: "Target yield",
        before: "36 g",
        after: null,
        from_profile: false,
      },
      { field: "dose_g", label: "Dose", before: null, after: "18 g", from_profile: false },
    ];
    renderWithQueryClient(
      <VersionTimeline setId={3} versions={detail.versions} judgements={detail.judgements} />,
    );

    // "36 g → —" reads as a rendering bug; unsetting a field is a deliberate
    // change and the word is what makes it one.
    const changes = await screen.findByTestId("version-changes");
    expect(changes).toHaveTextContent("36 g");
    expect(changes).toHaveTextContent("cleared");
    expect(changes).toHaveTextContent("not set");
  });

  it("says a temperature change came with the profile", async () => {
    const detail = setDetail();
    detail.versions[0].changes = [
      {
        field: "profile_version_id",
        label: "Profile",
        before: "9 Bar Espresso",
        after: "9 Bar Espresso hotter",
        from_profile: false,
      },
      {
        field: "profile_temperature_c",
        label: "Temperature",
        before: "93 °C",
        after: "94 °C",
        from_profile: true,
      },
    ];
    renderWithQueryClient(
      <VersionTimeline setId={3} versions={detail.versions} judgements={detail.judgements} />,
    );

    // There is no Temperature field on the form, so a line that did not say
    // where it came from would read as a bug. In words, not in a colour.
    const carried = await screen.findByTestId("change-from-profile");
    expect(carried).toHaveTextContent("Temperature");
    expect(carried).toHaveTextContent("93 °C");
    expect(carried).toHaveTextContent("94 °C");
    expect(carried).toHaveTextContent("from the profile");
    // The profile change itself is nobody's but the person's.
    expect(screen.getAllByTestId("change-from-profile")).toHaveLength(1);
  });

  it("does not call a profile with no temperature a cleared field", async () => {
    const detail = setDetail();
    detail.versions[0].changes = [
      {
        field: "profile_temperature_c",
        label: "Temperature",
        before: "93 °C",
        after: null,
        from_profile: true,
      },
    ];
    renderWithQueryClient(
      <VersionTimeline setId={3} versions={detail.versions} judgements={detail.judgements} />,
    );

    // Nobody cleared anything: the profile this version switched to states no
    // temperature, and "cleared" would accuse somebody of an edit.
    const carried = await screen.findByTestId("change-from-profile");
    expect(carried).toHaveTextContent("no temperature stated");
    expect(carried).not.toHaveTextContent("cleared");
  });

  it("lists the shots under the version they were pulled with", () => {
    const detail = setDetail();
    detail.versions[0].shots = [
      {
        id: 41,
        device_id: "000141",
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
      <VersionTimeline setId={3} versions={detail.versions} judgements={detail.judgements} />,
    );

    const shots = screen.getByTestId("version-shots");
    expect(shots).toHaveTextContent("9 Bar Espresso");
    expect(screen.getByTestId("rating-stars")).toHaveAttribute("data-rating", "5");
  });

  it("reads as an experiment: prediction, labels, outcome, restores", async () => {
    const detail = setDetail();
    detail.versions[0].version = version({
      id: 22,
      version_no: 2,
      parent_version_id: 21,
      shot_count: 3,
      prediction: "less bitter, a shorter shot",
      compares_to_version_id: 21,
      compares_to_version_no: 1,
      restores_version_id: 21,
      restores_version_no: 1,
      outcome: "partly_held",
      outcome_note: "shorter, still sharp",
      outcome_state: "partly_held",
    });
    detail.versions[0].labels = labelCounts({ keep: 2, improve: 1 });
    renderWithQueryClient(
      <VersionTimeline setId={3} versions={detail.versions} judgements={detail.judgements} />,
    );

    const entry = screen.getAllByTestId("version-entry")[0];
    expect(entry).toHaveTextContent("less bitter, a shorter shot");
    expect(entry).toHaveTextContent("compared to v1");
    expect(entry).toHaveTextContent("2 Keep · 1 Improve");
    expect(entry).toHaveTextContent("Restores v1");
    expect(screen.getAllByTestId("version-outcome")[0]).toHaveAttribute(
      "data-state",
      "partly_held",
    );
    // The word comes from the served vocabulary, like every other closed set.
    expect(await screen.findByText("Partly held")).toBeInTheDocument();
    expect(entry).toHaveTextContent("shorter, still sharp");
    // A version with shots offers no prediction editor: the server refuses it.
    expect(screen.queryByRole("button", { name: "Edit prediction" })).not.toBeInTheDocument();
  });

  it("reads a profile-only version as a change, not as nothing changed", () => {
    const detail = setDetail();
    // The diff the server computed for a version whose only difference is the
    // profile it was brewed with.
    detail.versions[0].changes = [
      {
        field: "profile_version_id",
        label: "Profile",
        before: "9 Bar Espresso",
        after: "Turbo",
        from_profile: false,
      },
    ];
    renderWithQueryClient(
      <VersionTimeline setId={3} versions={detail.versions} judgements={detail.judgements} />,
    );

    const changes = screen.getByTestId("version-changes");
    expect(changes).toHaveTextContent("Profile");
    expect(changes).toHaveTextContent("9 Bar Espresso");
    expect(changes).toHaveTextContent("Turbo");
    expect(screen.queryByText(/Nothing in the recipe changed/)).not.toBeInTheDocument();
  });

  it("links a version's shot count at that version's shots", () => {
    const detail = setDetail();
    detail.versions[0].version = version({ id: 22, version_no: 2, shot_count: 3 });
    renderWithQueryClient(
      <VersionTimeline setId={3} versions={detail.versions} judgements={detail.judgements} />,
    );

    // The count beside it is this version's, so the link has to be too.
    expect(screen.getAllByRole("link", { name: /3 shots/ })[0]).toHaveAttribute(
      "href",
      "/shots?set=3&version=22",
    );
  });

  it("links each version at the conversation where that change is argued", () => {
    const detail = setDetail();
    detail.versions[0].version = version({ id: 22, version_no: 2 });
    renderWithQueryClient(
      <VersionTimeline setId={3} versions={detail.versions} judgements={detail.judgements} />,
    );

    // The version, not the Set: there is one conversation per change, and the
    // Chat page opens or continues that one.
    expect(screen.getByRole("link", { name: "Chat about v2" })).toHaveAttribute(
      "href",
      "/chat?set=3&version=22",
    );
  });

  it("mutes a version a later roll back stepped over, and still shows it", () => {
    const detail = setDetail();
    detail.versions[0].dead_end = true;
    renderWithQueryClient(
      <VersionTimeline setId={3} versions={detail.versions} judgements={detail.judgements} />,
    );

    const entries = screen.getAllByTestId("version-entry");
    expect(entries[0]).toHaveAttribute("data-dead-end", "yes");
    expect(entries[1]).toHaveAttribute("data-dead-end", "no");
    expect(screen.getByTestId("dead-end")).toBeInTheDocument();
    // Muted, not hidden: it was a real attempt.
    expect(entries[0]).toHaveTextContent("one click finer, chasing the sourness out");
  });

  it("records a prediction while the version has no shots", async () => {
    const user = setupUser();
    const detail = setDetail();
    detail.versions[0].version = version({
      id: 22,
      version_no: 2,
      parent_version_id: 21,
      shot_count: 0,
    });
    renderWithQueryClient(
      <VersionTimeline setId={3} versions={detail.versions} judgements={detail.judgements} />,
    );

    await user.click(screen.getAllByRole("button", { name: "Add a prediction" })[0]);
    await user.type(screen.getByLabelText("Version prediction"), "less sour");
    await user.selectOptions(screen.getByLabelText("Compared to"), "21");
    await user.click(screen.getByRole("button", { name: "Save the prediction" }));

    await waitFor(() => expect(setVersionPrediction).toHaveBeenCalled());
    expect(setVersionPrediction.mock.calls[0]).toEqual([
      3,
      22,
      { prediction: "less sour", compares_to_version_id: 21 },
    ]);
  });

  it("will not offer a grade when there is nothing to grade, and says why in text", async () => {
    const detail = setDetail();
    detail.versions[0].version = version({
      id: 22,
      version_no: 2,
      prediction: "less bitter",
      outcome_state: "open",
    });
    detail.versions[0].labels = labelCounts({ discard: 1 });
    renderWithQueryClient(
      <VersionTimeline setId={3} versions={detail.versions} judgements={detail.judgements} />,
    );

    const buttons = screen.getAllByRole("button", { name: "Record the outcome" });
    expect(buttons[0]).toBeDisabled();
    // The reason is text on the page tied to the button, not a `title` on a
    // control nothing can focus.
    const described = document.getElementById(buttons[0].getAttribute("aria-describedby") ?? "");
    expect(described).toHaveTextContent("Label a shot Keep or Improve first");
    // And on the version that predicted nothing, the reason is the other one.
    const other = document.getElementById(buttons[1].getAttribute("aria-describedby") ?? "");
    expect(other).toHaveTextContent("states no prediction");
  });

  it("still lets a grade be cleared once its shots are gone", async () => {
    const user = setupUser();
    const detail = setDetail();
    detail.versions[0].version = version({
      id: 22,
      version_no: 2,
      prediction: "less bitter",
      outcome: "held",
      outcome_state: "held",
    });
    // The shot that was graded has since been unfiled: the server will still
    // clear the grade, and refuses to change it.
    detail.versions[0].labels = labelCounts();
    renderWithQueryClient(
      <VersionTimeline setId={3} versions={detail.versions} judgements={detail.judgements} />,
    );

    // Named for what it offers: "Change" would promise a control the server
    // refuses on a version with nothing left to grade.
    const trigger = screen.getAllByRole("button", { name: "Clear the outcome" })[0];
    expect(trigger).toBeEnabled();
    await user.click(trigger);

    expect(screen.getByTestId("outcome-clear-only")).toHaveTextContent("can still be taken back");
    expect(screen.queryByRole("button", { name: "Save the outcome" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Clear" }));

    await waitFor(() => expect(clearVersionOutcome).toHaveBeenCalledWith(3, 22));
  });

  it("names the group the four grades belong to", async () => {
    const user = setupUser();
    const detail = setDetail();
    detail.versions[0].version = version({
      id: 22,
      version_no: 2,
      prediction: "less bitter",
      outcome_state: "open",
    });
    detail.versions[0].labels = labelCounts({ keep: 1 });
    renderWithQueryClient(
      <VersionTimeline setId={3} versions={detail.versions} judgements={detail.judgements} />,
    );

    await user.click(screen.getAllByRole("button", { name: "Record the outcome" })[0]);

    expect(
      await screen.findByRole("group", { name: "How did the prediction turn out?" }),
    ).toBeInTheDocument();
  });

  it("defaults an untouched comparison to the parent, not to nothing", async () => {
    const user = setupUser();
    const detail = setDetail();
    // v2, no prediction yet: the stored comparison is null because there is no
    // prediction, and seeding the select from it would save "compared to
    // nothing" without anybody choosing that.
    detail.versions[0].version = version({
      id: 22,
      version_no: 2,
      parent_version_id: 21,
      shot_count: 0,
    });
    renderWithQueryClient(
      <VersionTimeline setId={3} versions={detail.versions} judgements={detail.judgements} />,
    );

    await user.click(screen.getAllByRole("button", { name: "Add a prediction" })[0]);
    expect(screen.getByLabelText("Compared to")).toHaveValue("21");
    await user.type(screen.getByLabelText("Version prediction"), "less sour");
    await user.click(screen.getByRole("button", { name: "Save the prediction" }));

    await waitFor(() => expect(setVersionPrediction).toHaveBeenCalled());
    expect(setVersionPrediction.mock.calls[0][2]).toEqual({
      prediction: "less sour",
      compares_to_version_id: 21,
    });
  });

  it("has nothing to default to on the first version", async () => {
    const user = setupUser();
    const detail = setDetail();
    detail.versions = [detail.versions[1]];
    detail.versions[0].version = version({ id: 21, version_no: 1, shot_count: 0 });
    renderWithQueryClient(
      <VersionTimeline setId={3} versions={detail.versions} judgements={detail.judgements} />,
    );

    await user.click(screen.getByRole("button", { name: "Add a prediction" }));
    expect(screen.getByLabelText("Compared to")).toHaveValue("");
    await user.type(screen.getByLabelText("Version prediction"), "a clean 1:2");
    await user.click(screen.getByRole("button", { name: "Save the prediction" }));

    await waitFor(() => expect(setVersionPrediction).toHaveBeenCalled());
    expect(setVersionPrediction.mock.calls[0][2]).toEqual({
      prediction: "a clean 1:2",
      compares_to_version_id: null,
    });
  });

  it("keeps a stored 'Nothing' when an existing prediction is edited", async () => {
    const user = setupUser();
    const detail = setDetail();
    detail.versions[0].version = version({
      id: 22,
      version_no: 2,
      parent_version_id: 21,
      shot_count: 0,
      prediction: "a clean 1:2",
      compares_to_version_id: null,
    });
    renderWithQueryClient(
      <VersionTimeline setId={3} versions={detail.versions} judgements={detail.judgements} />,
    );

    await user.click(screen.getAllByRole("button", { name: "Edit prediction" })[0]);

    // Somebody chose "Nothing" for this one; editing the wording must not
    // quietly move it onto the parent.
    expect(screen.getByLabelText("Compared to")).toHaveValue("");
  });

  it("offers no prediction editor while a grade stands, and says why", async () => {
    const detail = setDetail();
    detail.versions[0].version = version({
      id: 22,
      version_no: 2,
      shot_count: 0,
      prediction: "less bitter",
      outcome: "held",
      outcome_state: "held",
    });
    renderWithQueryClient(
      <VersionTimeline setId={3} versions={detail.versions} judgements={detail.judgements} />,
    );

    // The server answers this write with a conflict, so the button does not
    // pretend otherwise — and the way out is text a reader can reach.
    const button = screen.getByRole("button", { name: "Edit prediction" });
    expect(button).toBeDisabled();
    const described = document.getElementById(button.getAttribute("aria-describedby") ?? "");
    expect(described).toHaveTextContent("Clear the outcome first");
  });

  it("sends an explicit null when the prediction is compared to nothing", async () => {
    const user = setupUser();
    const detail = setDetail();
    detail.versions[0].version = version({
      id: 22,
      version_no: 2,
      parent_version_id: 21,
      shot_count: 0,
    });
    renderWithQueryClient(
      <VersionTimeline setId={3} versions={detail.versions} judgements={detail.judgements} />,
    );

    await user.click(screen.getAllByRole("button", { name: "Add a prediction" })[0]);
    // Opening the editor moves focus into it: the button that opened it is gone.
    expect(screen.getByLabelText("Version prediction")).toHaveFocus();
    await user.type(screen.getByLabelText("Version prediction"), "  a clean 1:2  ");
    await user.selectOptions(screen.getByLabelText("Compared to"), "");
    await user.click(screen.getByRole("button", { name: "Save the prediction" }));

    await waitFor(() => expect(setVersionPrediction).toHaveBeenCalled());
    // Null, not omitted: omitted would fall back to the parent, which is the
    // opposite of what "Nothing" says. And the text arrives trimmed.
    expect(setVersionPrediction.mock.calls[0][2]).toEqual({
      prediction: "a clean 1:2",
      compares_to_version_id: null,
    });
  });

  it("records, changes and clears an outcome", async () => {
    const user = setupUser();
    const detail = setDetail();
    detail.versions[0].version = version({
      id: 22,
      version_no: 2,
      prediction: "less bitter",
      outcome_state: "open",
    });
    detail.versions[0].labels = labelCounts({ keep: 1 });
    renderWithQueryClient(
      <VersionTimeline setId={3} versions={detail.versions} judgements={detail.judgements} />,
    );

    await user.click(screen.getAllByRole("button", { name: "Record the outcome" })[0]);
    await user.click(await screen.findByRole("button", { name: "Held" }));
    await user.type(screen.getByLabelText("Why"), "exactly that");
    await user.click(screen.getByRole("button", { name: "Save the outcome" }));

    await waitFor(() => expect(setVersionOutcome).toHaveBeenCalled());
    expect(setVersionOutcome.mock.calls[0]).toEqual([
      3,
      22,
      { outcome: "held", note: "exactly that" },
    ]);
  });

  it("clears a grade that was already given", async () => {
    const user = setupUser();
    const detail = setDetail();
    detail.versions[0].version = version({
      id: 22,
      version_no: 2,
      prediction: "less bitter",
      outcome: "held",
      outcome_state: "held",
    });
    detail.versions[0].labels = labelCounts({ keep: 1 });
    renderWithQueryClient(
      <VersionTimeline setId={3} versions={detail.versions} judgements={detail.judgements} />,
    );

    await user.click(screen.getAllByRole("button", { name: "Change the outcome" })[0]);
    await user.click(await screen.findByRole("button", { name: "Clear" }));

    await waitFor(() => expect(clearVersionOutcome).toHaveBeenCalledWith(3, 22));
  });

  it("asks before a roll back and says the machine is not written", async () => {
    const user = setupUser();
    const detail = setDetail();
    renderWithQueryClient(
      <VersionTimeline setId={3} versions={detail.versions} judgements={detail.judgements} />,
    );

    // Only on an old version: the newest one is where you already are.
    const triggers = screen.getAllByRole("button", { name: "Roll back to this version" });
    expect(triggers).toHaveLength(1);

    await user.click(triggers[0]);
    expect(screen.getByTestId("rollback-confirm")).toHaveTextContent(
      "Nothing is sent to the machine",
    );
    await user.click(screen.getByRole("button", { name: "Roll back to v1" }));

    await waitFor(() => expect(rollbackSet).toHaveBeenCalled());
    expect(rollbackSet.mock.calls[0]).toEqual([
      3,
      { to_version_id: 21, intent: "", prediction: "" },
    ]);
  });
});

describe("VersionTimeline, a Set being designed", () => {
  const empty = [
    { version: emptyVersion(), changes: [], shots: [], dead_end: false, labels: labelCounts() },
  ];

  it("reads version 1 as being designed, not as a row of empty fields", () => {
    renderWithQueryClient(<VersionTimeline setId={6} versions={empty} judgements={{}} designing />);

    const entry = screen.getByTestId("version-entry");
    expect(entry).toHaveAttribute("data-designing", "yes");
    expect(screen.getByTestId("version-being-designed")).toHaveTextContent(
      "being designed — no recipe yet",
    );
    // None of an experiment's furniture: there is no recipe to have predicted,
    // graded or brewed yet.
    expect(entry).not.toHaveTextContent("nothing recorded yet");
    expect(entry).not.toHaveTextContent("No prediction.");
    expect(entry).not.toHaveTextContent("No shots on this version yet.");
    expect(screen.queryByTestId("version-labels")).not.toBeInTheDocument();
    expect(screen.getByTestId("version-chat").querySelector("a")).toHaveAttribute(
      "href",
      "/chat?set=6&version=60",
    );
  });

  it("still counts a shot somebody filed on it by hand", () => {
    const withShot = [{ ...empty[0], version: emptyVersion({ shot_count: 1 }) }];
    renderWithQueryClient(
      <VersionTimeline setId={6} versions={withShot} judgements={{}} designing />,
    );

    expect(screen.getByTestId("version-labels")).toHaveTextContent("1 shot filed here by hand");
  });

  it("renders a filled version 1 as any version 1, even before the flag has caught up", () => {
    const filled = [{ ...empty[0], version: emptyVersion({ dose_g: 18, grind_setting: "20" }) }];
    renderWithQueryClient(
      <VersionTimeline setId={6} versions={filled} judgements={{}} designing />,
    );

    expect(screen.queryByTestId("version-being-designed")).not.toBeInTheDocument();
    expect(screen.getByTestId("version-entry")).toHaveTextContent("18 g in");
  });

  it("leaves an empty version 1 alone on a Set that is not being designed", () => {
    // A hand-made "any profile" Set with nothing typed is a Set, not a design.
    renderWithQueryClient(<VersionTimeline setId={6} versions={empty} judgements={{}} />);

    expect(screen.queryByTestId("version-being-designed")).not.toBeInTheDocument();
    expect(screen.getByTestId("version-entry")).toHaveTextContent("nothing recorded yet");
  });
});
