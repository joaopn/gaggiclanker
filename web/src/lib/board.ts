import type { BoardAction, BoardRowView, BoardView, SyncRunRow } from "@/api/types";

/**
 * The profile list in the words a person reads.
 *
 * The server plans what the next sync would do (`BoardView`); everything here only
 * words it. Nothing decides what a sync does, so what the page says and what the sync
 * does cannot drift apart.
 */

/** The server's reason for two live profiles sharing a label. */
const SHARED_LABEL = "duplicate_label";

export type RowTone = "ok" | "info" | "warn";

export type RowState = {
  /** What stands or will happen on the machine for this profile, in one phrase. */
  text: string;
  tone: RowTone;
  /** Further sentences: why a sync will not do something, the old copy that goes, the star. */
  notes: string[];
};

/** The plain-words reason the server gives for a plan entry, or the entry's own detail. */
function reasonWords(reason: string): string {
  switch (reason) {
    case "missing":
      return "it was not on the machine";
    case "superseded":
      return "a newer version replaced it";
    case "off":
      return "it is switched off";
    case "edited_on_machine":
      return "it was edited on the display";
    case "first_pull":
      return "taken from the machine as it was";
    default:
      return reason.replaceAll("_", " ");
  }
}

/** What a report on a profile means to a person; the server's own detail follows as a note. */
const REPORT_WORDS: Record<string, string> = {
  missing: "Missing from the machine",
  unreadable: "The machine would not give it up to be read",
  did_not_verify: "Did not verify, not tried again",
  policy: "Outside the safety bounds",
  extra_copy: "The machine holds a second copy",
};

/**
 * Where one profile of the list stands on the machine, and what the next sync will do.
 *
 * The server plans (`planned`, `reports`); this only words it. The order says the most
 * important thing first: a conflict (the sync leaves the profile alone), a problem the sync
 * will not fix, then what the sync will do, then how things stand now.
 */
export function rowStateOf(view: BoardView, entry: BoardRowView): RowState {
  const id = entry.row.id;
  const on = entry.on_machine;
  const present = entry.machine.present;
  const notes: string[] = [];
  const planned = entry.planned ?? [];
  const report = view.reports?.find(
    (r) => r.row_id === id && r.reason !== SHARED_LABEL && r.reason !== "conflict",
  );
  const shared = view.reports?.find((r) => r.row_id === id && r.reason === SHARED_LABEL);
  const push = planned.find((a) => a.kind === "push");
  const remove = planned.find((a) => a.kind === "remove");
  let state: RowState;

  if (entry.in_conflict) {
    state = { text: "Edited outside the app: choose a side", tone: "warn", notes };
    notes.push("A sync does nothing for this profile until you choose.");
  } else if (report) {
    state = { text: REPORT_WORDS[report.reason] ?? "Needs a look", tone: "warn", notes };
    if (report.detail) notes.push(sentence(report.detail));
  } else if (!on) {
    state =
      present && remove
        ? { text: "Will be removed at the next sync", tone: "info", notes }
        : present
          ? { text: "On the machine, and switched off", tone: "warn", notes }
          : { text: "Not on the machine", tone: "ok", notes };
  } else if (push) {
    state = { text: "Will be put on the machine at the next sync", tone: "info", notes };
  } else if (entry.machine.holds_current) {
    state = { text: "On the machine", tone: "ok", notes };
  } else if (present) {
    state = { text: "On the machine, but not this version", tone: "warn", notes };
  } else {
    state = { text: "Not on the machine", tone: "warn", notes };
  }

  if (shared) notes.push(sentence(shared.detail));
  for (const action of planned) {
    if (action.kind === "leave") {
      notes.push(
        `${action.device_id ? `The copy on the machine (${action.device_id}) stays` : "It stays on the machine"}: ${action.detail || reasonWords(action.reason)}.`,
      );
    } else if (action.kind === "remove" && on) {
      notes.push(
        `The older copy${action.device_id ? ` (${action.device_id})` : ""} will be removed.`,
      );
    } else if (action.kind === "home_screen" && on) {
      notes.push(
        action.on ? "It will be starred on the machine." : "Its star will come off on the machine.",
      );
    }
  }
  if (entry.row.pending_set_id != null) {
    notes.push(
      "Once a sync has put it on the machine it is recorded as the next version of its Set.",
    );
  }
  if (!on && entry.machine.selected && present) {
    notes.push(
      "It is the machine's selected profile: a sync selects another enabled profile first, then removes it.",
    );
  }
  return state;
}

function sentence(text: string): string {
  const trimmed = text.trim();
  if (trimmed === "") return "";
  const capital = trimmed[0]?.toUpperCase() + trimmed.slice(1);
  return /[.!?]$/.test(capital) ? capital : `${capital}.`;
}

/** Where a version came from, in the words the version list uses. */
export function sourceWords(source: string): string {
  switch (source) {
    case "agent":
      return "Proposed by the agent";
    case "edit":
      return "Edited in the app";
    case "machine":
      return "Read from the machine";
    case "edited_on_machine":
      return "Edited on the machine";
    case "import":
      return "Imported from a file";
    default:
      return source.replaceAll("_", " ");
  }
}

export type PreviewCounts = {
  /** Files the sync would take into the list (new profiles, or attached to one). */
  adopt: number;
  push: number;
  remove: number;
  homeScreen: number;
  leave: number;
};

/** How many of each thing the next sync would do. */
export function previewCounts(view: BoardView): PreviewCounts {
  const count = (kind: BoardAction["kind"]) =>
    (view.actions ?? []).filter((a) => a.kind === kind).length;
  return {
    adopt: count("adopt"),
    push: count("push"),
    remove: count("remove"),
    homeScreen: count("home_screen"),
    leave: count("leave"),
  };
}

/** One line per planned action, for the switch's preview and the reset banner. */
export function previewLine(action: BoardAction, options: { rowIsOff?: boolean } = {}): string {
  switch (action.kind) {
    case "push":
      return `Put ${action.label} on the machine`;
    case "remove":
      return `Remove ${action.label} from the machine`;
    case "home_screen":
      return `${action.on ? "Star" : "Take the star off"} ${action.label}`;
    case "leave":
      return `Leave ${action.label} on the machine`;
    case "adopt":
      // A file the list already has a profile for is matched to it, not added.
      if (action.reason === "attached") {
        // A file joining a profile that is off is taken off the machine by the sync after the one
        // that matches it (a file is never removed by the sync that finds it).
        return options.rowIsOff
          ? `The machine's copy of ${action.label} is matched to ${action.label}, which is off: the next sync removes it`
          : `The machine's copy of ${action.label} is matched to ${action.label}`;
      }
      if (action.reason === "conflict") {
        return `The machine's copy of ${action.label} differs from the app's: it becomes a conflict to settle`;
      }
      return `Add ${action.label} to the list`;
    default:
      return action.label;
  }
}

// ── a sync's summary ──────────────────────────────────────────────

export type BoardSummaryItem = {
  label: string;
  reason: string;
  detail: string;
  on: boolean | null;
  /** A push that found the identical file on the machine and sent no save. */
  reused?: boolean;
};

/** What the last sync's profile pass did to the machine: `SyncRunRow.summary` for the `profiles` run. */
export type BoardRunSummary = {
  adopted: BoardSummaryItem[];
  conflicts: BoardSummaryItem[];
  recorded: BoardSummaryItem[];
  pushed: BoardSummaryItem[];
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
        reused: item.reused === true,
      },
    ];
  });
}

/**
 * Read a run's summary; `null` for a run that has none of the write phase's: any pass but a
 * profile pass, and a profile pass with the switch off, whose summary holds only the count of
 * profiles read. The write phase always records `writes`, so that is what tells them apart.
 */
export function boardSummaryOf(run: SyncRunRow | undefined): BoardRunSummary | null {
  const raw = run?.summary;
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) return null;
  const s = raw as Record<string, unknown>;
  if (typeof s.writes !== "number") return null;
  return {
    adopted: items(s.adopted),
    conflicts: items(s.conflicts),
    recorded: items(s.recorded),
    pushed: items(s.pushed),
    removed: items(s.removed),
    left: items(s.left),
    homeScreen: items(s.home_screen),
    failures: items(s.failures),
    writes: typeof s.writes === "number" ? s.writes : 0,
    paused: typeof s.paused === "string" ? s.paused : null,
  };
}

type SummarySection =
  | "conflicts"
  | "recorded"
  | "pushed"
  | "removed"
  | "left"
  | "homeScreen"
  | "failures"
  | "adopted";

/**
 * What a reason means *in the section it is listed under*: the same code reads differently
 * for a push ("superseded" is the new version arriving) and for a removal or a left file
 * (it is the old one going or staying).
 */
const SECTION_REASONS: Record<SummarySection, Record<string, string>> = {
  conflicts: {},
  recorded: { edited_on_machine: "its content was edited on the display and is now a version" },
  pushed: {
    missing: "it was not on the machine",
    superseded: "it replaced its older version",
    edited_on_machine: "it was put beside a copy edited on the display",
  },
  removed: {
    superseded: "the old copy went after a newer version was put on",
    off: "it is switched off",
  },
  left: {
    superseded: "the old copy stays although a newer version replaced it",
    off: "it is switched off but stays on the machine",
  },
  adopted: {
    first_pull: "taken from the machine as it was",
    unseen: "new on the machine, added to the list and switched on",
    attached: "added to the profile it belongs to",
    conflict: "it differs from every version the app has: choose a side on the Profiles page",
  },
  homeScreen: {},
  failures: {},
};

/** One summary line in words: the profile, why (by section), then what else was recorded. */
export function summaryLine(item: BoardSummaryItem, section: SummarySection): string {
  if (section === "failures") {
    const step = item.reason ? reasonWords(item.reason) : "";
    return [item.label, [step, item.detail].filter(Boolean).join(": ")].filter(Boolean).join(": ");
  }
  if (section === "homeScreen") {
    return `${item.label}: ${item.on === null ? "star changed" : item.on ? "put on the home screen" : "taken off the home screen"}`;
  }
  const reason = item.reason
    ? (SECTION_REASONS[section][item.reason] ?? reasonWords(item.reason))
    : "";
  const parts = [reason ? sentence(reason) : "", item.detail ? sentence(item.detail) : ""].filter(
    Boolean,
  );
  return parts.length > 0 ? `${item.label}: ${parts.join(" ")}` : item.label;
}
