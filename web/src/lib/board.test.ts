import { describe, expect, it } from "vitest";
import {
  boardSummaryOf,
  previewCounts,
  previewLine,
  rowStateOf,
  sourceWords,
  summaryLine,
} from "@/lib/board";
import { boardAction, boardRowView, boardView } from "@/test/boardFixtures";

describe("rowStateOf", () => {
  it("says a profile the machine holds as the list has it is on the machine", () => {
    const entry = boardRowView();
    expect(rowStateOf(boardView({ rows: [entry] }), entry)).toMatchObject({
      text: "On the machine",
      tone: "ok",
    });
  });

  it("says a planned push will happen at the next sync", () => {
    const entry = boardRowView({
      row: { id: 2 },
      machine: { present: false, holds_current: false },
      planned: [boardAction({ row_id: 2 })],
    });
    expect(rowStateOf(boardView({ rows: [entry] }), entry).text).toBe(
      "Will be put on the machine at the next sync",
    );
  });

  it("says a profile that is off and still on the machine will be removed", () => {
    const entry = boardRowView({
      row: { on_machine: false },
      planned: [boardAction({ kind: "remove", row_id: 1, reason: "off", device_id: "9bar" })],
    });
    expect(rowStateOf(boardView({ rows: [entry] }), entry).text).toBe(
      "Will be removed at the next sync",
    );
  });

  it("never says 'Not on the machine' for a profile that is on while a file is being matched to it", () => {
    const entry = boardRowView({
      row: { id: 3 },
      machine: { present: false, holds_current: false },
    });
    const view = boardView({
      rows: [entry],
      actions: [boardAction({ kind: "adopt", reason: "attached", row_id: 3, label: "X" })],
    });
    expect(rowStateOf(view, entry).text).toBe("On the machine");
  });

  it("says an off profile whose file is on the machine is on it, never 'Not on the machine'", () => {
    const entry = boardRowView({ row: { on_machine: false } });
    expect(rowStateOf(boardView({ rows: [entry] }), entry).text).toBe(
      "On the machine, switched off: the next sync removes it",
    );
  });

  it("says a profile that is off and gone from the machine is not on it", () => {
    const entry = boardRowView({
      row: { on_machine: false },
      machine: { present: false, holds_current: false },
    });
    expect(rowStateOf(boardView({ rows: [entry] }), entry)).toMatchObject({
      text: "Not on the machine",
      tone: "ok",
    });
  });

  it("gives the reason a sync leaves a file where it is", () => {
    const entry = boardRowView({
      row: { on_machine: false },
      planned: [
        boardAction({
          kind: "leave",
          row_id: 1,
          reason: "off",
          device_id: "9bar",
          detail: "it is the profile the machine has selected and no other is on",
        }),
      ],
    });
    const state = rowStateOf(boardView({ rows: [entry] }), entry);
    expect(state.text).not.toBe("Will be removed at the next sync");
    expect(state.notes).toContain(
      "The copy on the machine (9bar) stays: it is the profile the machine has selected and no other is on.",
    );
  });

  it("says a conflict first and that the sync does nothing for the profile", () => {
    const entry = boardRowView({ in_conflict: true });
    const state = rowStateOf(
      boardView({
        rows: [entry],
        reports: [boardAction({ kind: "report", row_id: 1, reason: "conflict" })],
      }),
      entry,
    );
    expect(state.text).toBe("Edited outside the app: choose a side");
    expect(state.notes).toEqual(["A sync does nothing for this profile until you choose."]);
  });

  it("says a shared label as a note and keeps the profile's own state", () => {
    const entry = boardRowView({ row: { id: 2 } });
    const view = boardView({
      rows: [entry],
      reports: [
        boardAction({
          kind: "report",
          row_id: 2,
          reason: "duplicate_label",
          detail: "another profile is also called Londinium",
        }),
      ],
    });
    expect(rowStateOf(view, entry)).toMatchObject({
      text: "On the machine",
      notes: ["Another profile is also called Londinium."],
    });
  });

  it("names the older copy that goes and the star that moves", () => {
    const entry = boardRowView({
      row: { id: 2 },
      machine: { present: true, holds_current: false },
      planned: [
        boardAction({ row_id: 2, reason: "superseded" }),
        boardAction({ row_id: 2, kind: "remove", device_id: "old1", reason: "superseded" }),
        boardAction({ row_id: 2, kind: "home_screen", on: false }),
      ],
    });
    expect(rowStateOf(boardView({ rows: [entry] }), entry).notes).toEqual([
      "The older copy (old1) will be removed.",
      "Its star will come off on the machine.",
    ]);
  });

  it("says each report in its own words", () => {
    const entry = boardRowView({ machine: { present: false, holds_current: false } });
    const text = (reason: string) =>
      rowStateOf(
        boardView({ rows: [entry], reports: [boardAction({ kind: "report", row_id: 1, reason })] }),
        entry,
      ).text;
    expect(text("missing")).toBe("Missing from the machine");
    expect(text("did_not_verify")).toBe("Did not verify, not tried again");
    expect(text("policy")).toBe("Outside the safety bounds");
    expect(text("extra_copy")).toBe("The machine holds a second copy");
    expect(text("unreadable")).toMatch(/would not give/);
  });

  it("warns about the selected one while the profile is off, and says nothing about the star", () => {
    const entry = boardRowView({
      row: { on_machine: false },
      machine: { present: true, holds_current: true, selected: true },
    });
    const notes = rowStateOf(boardView({ rows: [entry] }), entry).notes;
    expect(notes.join(" ")).not.toContain("Starred");
    expect(notes.join(" ")).toContain("selects another enabled profile first");
  });
});

describe("a profile made active for a Set", () => {
  it("says the Set's version is recorded once a sync has put it on the machine", () => {
    const entry = boardRowView({ row: { pending_set_id: 3 } });
    expect(rowStateOf(boardView({ rows: [entry] }), entry).notes).toContain(
      "Once a sync has put it on the machine it is recorded as the next version of its Set.",
    );
  });
});

describe("a version that did not verify", () => {
  it("says it is not tried again and gives the server's reason as a note", () => {
    const entry = boardRowView({ machine: { present: false, holds_current: false } });
    const state = rowStateOf(
      boardView({
        rows: [entry],
        reports: [
          boardAction({
            kind: "report",
            row_id: 1,
            reason: "did_not_verify",
            detail: "this version did not read back as sent, so it is not tried again",
          }),
        ],
      }),
      entry,
    );
    expect(state.text).toBe("Did not verify, not tried again");
    expect(state.notes[0]).toContain("not tried again");
  });
});

describe("previewLine for a file that joins the list", () => {
  it("adds a file nobody has a profile for, and matches one a profile already has", () => {
    expect(
      previewLine(boardAction({ kind: "adopt", label: "Made on display", reason: "unseen" })),
    ).toBe("Add Made on display to the list");
    const attached = previewLine(
      boardAction({ kind: "adopt", label: "Londinium", reason: "attached" }),
    );
    expect(attached).toBe("The machine's copy of Londinium is matched to Londinium");
    expect(attached).not.toContain("Add");
    expect(
      previewLine(boardAction({ kind: "adopt", label: "Londinium", reason: "attached" }), {
        rowIsOff: true,
      }),
    ).toBe(
      "The machine's copy of Londinium is matched to Londinium, which is off: the next sync removes it",
    );
    expect(
      previewLine(boardAction({ kind: "adopt", label: "9 Bar", reason: "conflict" })),
    ).toContain("conflict");
  });
});

describe("sourceWords", () => {
  it("says where a version came from in plain words", () => {
    expect(["agent", "edit", "machine", "edited_on_machine", "import"].map(sourceWords)).toEqual([
      "Proposed by the agent",
      "Edited in the app",
      "Read from the machine",
      "Edited on the machine",
      "Imported from a file",
    ]);
  });
});

describe("previewCounts", () => {
  it("counts each kind the next sync would do", () => {
    const view = boardView({
      actions: [
        boardAction({ kind: "push" }),
        boardAction({ kind: "push" }),
        boardAction({ kind: "remove" }),
        boardAction({ kind: "home_screen" }),
        boardAction({ kind: "leave" }),
      ],
    });
    expect(previewCounts(view)).toEqual({
      adopt: 0,
      push: 2,
      remove: 1,
      homeScreen: 1,
      leave: 1,
    });
  });
});

describe("boardSummaryOf", () => {
  const run = (summary: unknown) => ({ summary }) as never;

  it("is null for a run that has no summary", () => {
    expect(boardSummaryOf(undefined)).toBeNull();
    expect(boardSummaryOf(run(null))).toBeNull();
    expect(boardSummaryOf(run([]))).toBeNull();
  });

  it("is null for a profile pass with writes off, which only counts what it read", () => {
    expect(boardSummaryOf(run({ profiles_read: 9 }))).toBeNull();
  });

  it("keeps the board's summary when the count of profiles read sits beside it", () => {
    expect(boardSummaryOf(run({ profiles_read: 9, writes: 0, pushed: [] }))?.writes).toBe(0);
  });

  it("reads what a write phase recorded and tolerates what is missing", () => {
    const summary = boardSummaryOf(
      run({
        pushed: [{ label: "A [AI]", reason: "missing", detail: "", device_id: "x1" }],
        home_screen: [{ label: "B", reason: "on", on: true }],
        failures: [{ label: "C", reason: "round_trip", detail: "read back differently" }],
        writes: 3,
        paused: "the machine looks reset",
      }),
    );
    expect(summary?.pushed).toHaveLength(1);
    expect(summary?.removed).toEqual([]);
    expect(summary?.writes).toBe(3);
    expect(summary?.paused).toBe("the machine looks reset");
    expect(summaryLine(summary?.homeScreen[0] as never, "homeScreen")).toBe(
      "B: put on the home screen",
    );
    expect(summaryLine(summary?.failures[0] as never, "failures")).toBe(
      "C: round trip: read back differently",
    );
  });
});

describe("the event a sync's write phase sends", () => {
  it("refreshes every reader of what the phase changes", async () => {
    const { EVENT_INVALIDATIONS } = await import("@/lib/invalidate");
    const { queryKeys } = await import("@/lib/queryKeys");
    const keys = EVENT_INVALIDATIONS["profile.updated"] ?? [];
    for (const key of [
      queryKeys.profiles.all,
      queryKeys.sync.all,
      queryKeys.board.all,
      queryKeys.drafts.all,
      queryKeys.sets.all,
      queryKeys.device.all,
    ]) {
      expect(keys).toContainEqual([...key]);
    }
    expect(keys).toHaveLength(6);
  });

  it("reaches every proposal's standing, which is read under the drafts prefix", async () => {
    const { EVENT_INVALIDATIONS } = await import("@/lib/invalidate");
    const { queryKeys } = await import("@/lib/queryKeys");
    const keys = EVENT_INVALIDATIONS["profile.updated"] ?? [];
    const standing = queryKeys.drafts.standing(12);
    const reached = keys.some((key) =>
      key.every((part, index) => (standing as readonly unknown[])[index] === part),
    );
    expect(reached).toBe(true);
  });
});

describe("summaryLine reads a reason by the section it is listed under", () => {
  const item = (reason: string, detail = "") => ({ label: "P", reason, detail, on: null });
  const cases: [Parameters<typeof summaryLine>[1], string, string, string][] = [
    ["pushed", "missing", "", "P: It was not on the machine."],
    ["pushed", "superseded", "", "P: It replaced its older version."],
    ["pushed", "edited_on_machine", "", "P: It was put beside a copy edited on the display."],
    ["removed", "superseded", "", "P: The old copy went after a newer version was put on."],
    ["removed", "off", "", "P: It is switched off."],
    [
      "left",
      "superseded",
      "a Set is still brewing it",
      "P: The old copy stays although a newer version replaced it. A Set is still brewing it.",
    ],
    [
      "left",
      "off",
      "it is the selected profile",
      "P: It is switched off but stays on the machine. It is the selected profile.",
    ],
    ["adopted", "first_pull", "", "P: Taken from the machine as it was."],
    ["adopted", "unseen", "", "P: New on the machine, added to the list and switched on."],
    ["adopted", "attached", "", "P: Added to the profile it belongs to."],
    [
      "adopted",
      "conflict",
      "",
      "P: It differs from every version the app has: choose a side on the Profiles page.",
    ],
    [
      "recorded",
      "edited_on_machine",
      "",
      "P: Its content was edited on the display and is now a version.",
    ],
  ];
  it.each(cases)("%s / %s", (section, reason, detail, expected) => {
    expect(summaryLine(item(reason, detail), section)).toBe(expected);
  });

  it("puts the reason before the detail, never the detail instead of it", () => {
    const line = summaryLine(item("off", "the favourite star moved to the new copy"), "removed");
    expect(line.indexOf("switched off")).toBeLessThan(line.indexOf("favourite"));
    expect(line).toContain("favourite");
  });
});
