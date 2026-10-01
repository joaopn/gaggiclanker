import type { BoardAction, BoardRow, BoardRowView, BoardView, SyncRunRow } from "@/api/types";

/**
 * The profile board in the words a person reads.
 *
 * The server plans what the next pull would do (`BoardView`); everything here only
 * words it. Nothing decides what a pull does, so what the page says and what the pull
 * does cannot drift apart. No internal names reach the screen: a row made from a draft is
 * "the app's", one taken from the machine is "yours".
 */

/** Whose a profile is: the app wrote the machine's copy, or the person did. */
export function ownerOf(row: BoardRow): "app" | "yours" {
  return row.origin === "draft" ? "app" : "yours";
}

export type RowTone = "ok" | "info" | "warn";

export type RowState = {
  /** What happened or will happen to the profile on the machine, in one phrase. */
  text: string;
  tone: RowTone;
  /** Further sentences: the old copy that goes or stays, the star that changes, the server's reason. */
  notes: string[];
};

/** The plain-words reason the server gives for a plan entry, or the entry's own detail. */
function reasonWords(reason: string): string {
  switch (reason) {
    case "missing":
      return "it was not on the machine";
    case "superseded":
      return "a newer version replaced it";
    case "deleted":
      return "it was deleted";
    case "edited_on_machine":
      return "it was edited on the display";
    case "first_pull":
      return "taken from the machine as it was";
    default:
      return reason.replaceAll("_", " ");
  }
}

function actionFor(
  view: BoardView,
  rowId: number,
  kind: BoardAction["kind"],
): BoardAction | undefined {
  return view.actions?.find((a) => a.row_id === rowId && a.kind === kind);
}

/**
 * Where a live board profile stands on the machine.
 *
 * Order matters: a problem the pull will not fix (a profile of yours that was changed or
 * is gone) is said before anything else, then what the next pull will push, then the
 * plain "on the machine".
 */
export function rowStateOf(view: BoardView, entry: BoardRowView): RowState {
  const id = entry.row.id;
  const writesOn = view.writes_enabled;
  const report = view.reports?.find((r) => r.row_id === id);
  const push = entry.planned?.find((a) => a.kind === "push");
  const notes: string[] = [];
  let state: RowState;

  if (report) {
    const words: Record<string, string> = {
      edited_on_machine: "Edited on the display",
      missing: "Missing from the machine",
      unreadable: "The machine would not give it up to be read",
      did_not_verify: "Did not verify",
    };
    state = { text: words[report.reason] ?? "Needs a look", tone: "warn", notes };
    if (report.detail) notes.push(sentence(report.detail));
    if (report.reason === "edited_on_machine" && ownerOf(entry.row) === "yours") {
      notes.push("It is yours, so the app leaves it as you changed it.");
    }
  } else if (push) {
    const edited = push.reason === "edited_on_machine";
    state = {
      text: edited
        ? "Edited on the display"
        : writesOn
          ? "Will be pushed on the next pull"
          : "Will be pushed once writes are turned on",
      tone: "info",
      notes,
    };
    if (edited) {
      notes.push(
        writesOn
          ? "The board's version will be put beside it on the next pull."
          : "The board's version will be put beside it once writes are turned on.",
      );
    }
  } else if (entry.machine.holds_current) {
    state = { text: "On the machine", tone: "ok", notes };
  } else if (entry.machine.present) {
    state = { text: "On the machine, but not this version", tone: "warn", notes };
  } else {
    state = { text: "Not on the machine", tone: "warn", notes };
  }

  for (const action of entry.planned ?? []) {
    if (action.kind === "remove") {
      notes.push(`The old copy (${action.device_id}) will be removed.`);
    } else if (action.kind === "leave") {
      notes.push(
        `The old copy${action.device_id ? ` (${action.device_id})` : ""} stays on the machine: ${action.detail || reasonWords(action.reason)}.`,
      );
    } else if (action.kind === "home_screen") {
      notes.push(
        action.on ? "It will be put on the home screen." : "It will be taken off the home screen.",
      );
    }
  }
  return state;
}

/** A deleted profile whose file the pull still has to deal with, or `null` when nothing is left. */
export function deletedStateOf(view: BoardView, row: BoardRow): RowState | null {
  const removal = actionFor(view, row.id, "remove");
  if (removal) {
    return {
      text: view.writes_enabled
        ? "Will be removed from the machine on the next pull"
        : "Will be removed from the machine once writes are turned on",
      tone: "info",
      notes: [],
    };
  }
  const leave = actionFor(view, row.id, "leave");
  if (leave) {
    return {
      text: "Left on the machine",
      tone: "warn",
      notes: [sentence(leave.detail || reasonWords(leave.reason))],
    };
  }
  const report = view.reports?.find((r) => r.row_id === row.id);
  if (report) {
    return { text: "Left on the machine", tone: "warn", notes: [sentence(report.detail)] };
  }
  return null;
}

function sentence(text: string): string {
  const trimmed = text.trim();
  if (trimmed === "") return "";
  const capital = trimmed[0]?.toUpperCase() + trimmed.slice(1);
  return /[.!?]$/.test(capital) ? capital : `${capital}.`;
}

export type PreviewCounts = {
  push: number;
  remove: number;
  homeScreen: number;
  leave: number;
};

/** How many of each thing the next pull would do. */
export function previewCounts(view: BoardView): PreviewCounts {
  const count = (kind: BoardAction["kind"]) =>
    (view.actions ?? []).filter((a) => a.kind === kind).length;
  return {
    push: count("push"),
    remove: count("remove"),
    homeScreen: count("home_screen"),
    leave: count("leave"),
  };
}

/** One line per planned action, for the switch's preview. */
export function previewLine(action: BoardAction): string {
  switch (action.kind) {
    case "push":
      return `Push ${action.label}`;
    case "remove":
      return `Remove the old copy of ${action.label}`;
    case "home_screen":
      return `${action.on ? "Put" : "Take"} ${action.label} ${action.on ? "on" : "off"} the home screen`;
    case "leave":
      return `Leave ${action.label} on the machine`;
    default:
      return action.label;
  }
}

// ── a pull's summary ──────────────────────────────────────────────

export type BoardSummaryItem = {
  label: string;
  reason: string;
  detail: string;
  on: boolean | null;
};

/** What the last pull's profile pass did to the machine: `SyncRunRow.summary` for the `profiles` run. */
export type BoardRunSummary = {
  adopted: BoardSummaryItem[];
  pushed: BoardSummaryItem[];
  overwritten: BoardSummaryItem[];
  removed: BoardSummaryItem[];
  left: BoardSummaryItem[];
  homeScreen: BoardSummaryItem[];
  failures: BoardSummaryItem[];
  writes: number;
  paused: string | null;
};

function items(value: unknown): BoardSummaryItem[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((raw) => {
    if (typeof raw !== "object" || raw === null) return [];
    const item = raw as Record<string, unknown>;
    return [
      {
        label: typeof item.label === "string" ? item.label : "a profile",
        reason: typeof item.reason === "string" ? item.reason : "",
        detail: typeof item.detail === "string" ? item.detail : "",
        on: typeof item.on === "boolean" ? item.on : null,
      },
    ];
  });
}

/** Read a run's summary; `null` for a run that has none (any pass but a write phase). */
export function boardSummaryOf(run: SyncRunRow | undefined): BoardRunSummary | null {
  const raw = run?.summary;
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) return null;
  const s = raw as Record<string, unknown>;
  return {
    adopted: items(s.adopted),
    pushed: items(s.pushed),
    overwritten: items(s.overwritten),
    removed: items(s.removed),
    left: items(s.left),
    homeScreen: items(s.home_screen),
    failures: items(s.failures),
    writes: typeof s.writes === "number" ? s.writes : 0,
    paused: typeof s.paused === "string" ? s.paused : null,
  };
}

/** One summary line in words: the profile, and why or what came of it. */
export function summaryLine(
  item: BoardSummaryItem,
  section: "pushed" | "removed" | "left" | "homeScreen" | "failures" | "adopted" | "overwritten",
): string {
  if (section === "failures") {
    const step = item.reason ? reasonWords(item.reason) : "";
    return [item.label, [step, item.detail].filter(Boolean).join(": ")].filter(Boolean).join(": ");
  }
  if (section === "homeScreen") {
    return `${item.label}: ${item.on === null ? "star changed" : item.on ? "put on the home screen" : "taken off the home screen"}`;
  }
  const why = item.detail
    ? sentence(item.detail)
    : item.reason
      ? sentence(reasonWords(item.reason))
      : "";
  return why ? `${item.label}: ${why}` : item.label;
}
