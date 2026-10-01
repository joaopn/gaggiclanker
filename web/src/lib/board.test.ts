import { describe, expect, it } from "vitest";
import {
  boardSummaryOf,
  deletedStateOf,
  ownerOf,
  previewCounts,
  rowStateOf,
  summaryLine,
} from "@/lib/board";
import { boardAction, boardRow, boardRowView, boardView } from "@/test/boardFixtures";

describe("ownerOf", () => {
  it("a profile the app wrote is the app's and one taken from the machine is yours", () => {
    expect(ownerOf(boardRow({ origin: "draft" }))).toBe("app");
    expect(ownerOf(boardRow({ origin: "adopted" }))).toBe("yours");
  });
});

describe("rowStateOf", () => {
  it("says a profile the machine holds as the board has it is on the machine", () => {
    const entry = boardRowView();
    expect(rowStateOf(boardView({ rows: [entry] }), entry)).toMatchObject({
      text: "On the machine",
      tone: "ok",
    });
  });

  it("says a planned push will happen on the next pull, or when writes are on", () => {
    const entry = boardRowView({
      row: { id: 2, origin: "draft" },
      machine: { present: false, holds_current: false },
      planned: [boardAction({ row_id: 2 })],
    });
    expect(rowStateOf(boardView({ rows: [entry] }), entry).text).toBe(
      "Will be pushed on the next pull",
    );
    expect(rowStateOf(boardView({ rows: [entry], writes_enabled: false }), entry).text).toBe(
      "Will be pushed once writes are turned on",
    );
  });

  it("names the old copy that goes, the one that stays and the star that moves", () => {
    const entry = boardRowView({
      row: { id: 2, origin: "draft" },
      machine: { present: true, holds_current: false },
      planned: [
        boardAction({ row_id: 2, reason: "superseded" }),
        boardAction({ row_id: 2, kind: "remove", device_id: "old1", reason: "superseded" }),
        boardAction({
          row_id: 2,
          kind: "leave",
          device_id: "old2",
          detail: "a Set is still brewing it",
        }),
        boardAction({ row_id: 2, kind: "home_screen", on: false }),
      ],
    });
    const state = rowStateOf(boardView({ rows: [entry] }), entry);
    expect(state.notes).toEqual([
      "The old copy (old1) will be removed.",
      "The old copy (old2) stays on the machine: a Set is still brewing it.",
      "It will be taken off the home screen.",
    ]);
  });

  it("says a profile changed on the display is edited, and that the app leaves one of yours", () => {
    const entry = boardRowView({ machine: { present: true, holds_current: false } });
    const view = boardView({
      rows: [entry],
      reports: [
        boardAction({
          kind: "report",
          row_id: 1,
          reason: "edited_on_machine",
          detail: "9bar was changed on the machine since it was recorded",
        }),
      ],
    });
    const state = rowStateOf(view, entry);
    expect(state.text).toBe("Edited on the display");
    expect(state.tone).toBe("warn");
    expect(state.notes).toContain("It is yours, so the app leaves it as you changed it.");
  });

  it("says missing, did not verify and unreadable in their own words", () => {
    const entry = boardRowView({ machine: { present: false, holds_current: false } });
    const text = (reason: string) =>
      rowStateOf(
        boardView({ rows: [entry], reports: [boardAction({ kind: "report", row_id: 1, reason })] }),
        entry,
      ).text;
    expect(text("missing")).toBe("Missing from the machine");
    expect(text("did_not_verify")).toBe("Did not verify");
    expect(text("unreadable")).toMatch(/could not be read|would not give/);
  });

  it("says a profile with nothing planned that the machine lacks is not on it", () => {
    const entry = boardRowView({ machine: { present: false, holds_current: false } });
    expect(rowStateOf(boardView({ rows: [entry] }), entry).text).toBe("Not on the machine");
  });
});

describe("deletedStateOf", () => {
  it("says a deleted profile will be removed, or is left and why, or nothing when nothing is left", () => {
    const row = boardRow({ id: 5, deleted_at: "2026-03-02T00:00:00.000Z" });
    const view = (actions: ReturnType<typeof boardAction>[]) => boardView({ actions });
    expect(deletedStateOf(view([boardAction({ kind: "remove", row_id: 5 })]), row)?.text).toBe(
      "Will be removed from the machine on the next pull",
    );
    expect(
      deletedStateOf(
        view([
          boardAction({ kind: "leave", row_id: 5, detail: "it is not a profile the app wrote" }),
        ]),
        row,
      ),
    ).toMatchObject({
      text: "Left on the machine",
      notes: ["It is not a profile the app wrote."],
    });
    expect(deletedStateOf(view([]), row)).toBeNull();
  });
});

describe("previewCounts", () => {
  it("counts each kind the next pull would do", () => {
    const view = boardView({
      actions: [
        boardAction({ kind: "push" }),
        boardAction({ kind: "push" }),
        boardAction({ kind: "remove" }),
        boardAction({ kind: "home_screen" }),
        boardAction({ kind: "leave" }),
      ],
    });
    expect(previewCounts(view)).toEqual({ push: 2, remove: 1, homeScreen: 1, leave: 1 });
  });
});

describe("boardSummaryOf", () => {
  const run = (summary: unknown) => ({ summary }) as never;

  it("is null for a run that has no summary", () => {
    expect(boardSummaryOf(undefined)).toBeNull();
    expect(boardSummaryOf(run(null))).toBeNull();
    expect(boardSummaryOf(run([]))).toBeNull();
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
