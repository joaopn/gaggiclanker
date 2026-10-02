import type { BoardAction, BoardRow, BoardRowView, BoardView } from "@/api/types";

/** Factories for the profile board's tests. */

export function boardRow(overrides: Partial<BoardRow> = {}): BoardRow {
  return {
    id: 1,
    label: "9 Bar Espresso",
    current_version_id: 7,
    origin: "adopted",
    on_machine: true,
    on_home_screen: true,
    device_profile_id: "9bar",
    device_version_id: 7,
    created_at: "2026-03-01T00:00:00.000Z",
    updated_at: "2026-03-01T00:00:00.000Z",
    ...overrides,
  };
}

export function boardAction(overrides: Partial<BoardAction> = {}): BoardAction {
  return {
    kind: "push",
    label: "9 Bar Espresso [AI]",
    reason: "missing",
    detail: "",
    ...overrides,
  };
}

/** A standard profile document with one volumetric stop, for what a row says it brews. */
export function profileDocument(label = "9 Bar Espresso"): Record<string, unknown> {
  return {
    label,
    type: "standard",
    description: "",
    temperature: 93,
    utility: false,
    phases: [
      {
        name: "Pump",
        phase: "brew",
        valve: 1,
        duration: 28,
        temperature: 0,
        pump: { target: "pressure", pressure: 9, flow: 0 },
        transition: { type: "instant", duration: 0, adaptive: true },
        targets: [{ type: "volumetric", operator: "gte", value: 36 }],
      },
    ],
  };
}

/** A row on the machine as the board has it, with nothing planned. */
export function boardRowView(
  overrides: Partial<Omit<BoardRowView, "row">> & { row?: Partial<BoardRow> } = {},
): BoardRowView {
  const { row, ...rest } = overrides;
  const base = boardRow(row);
  return {
    row: base,
    type: "standard",
    utility: false,
    machine: {
      device_id: base.device_profile_id ?? null,
      present: true,
      holds_current: true,
      favorite: base.on_home_screen,
      selected: false,
    },
    planned: [],
    on_machine: base.on_machine,
    starred: base.on_home_screen,
    in_conflict: false,
    conflict: null,
    sets_brewing: [],
    proposed_versions: 0,
    active_version: {
      version_id: base.current_version_id,
      short_hash: "abcdef01",
      label: base.label,
      type: "standard",
      utility: false,
      source: "machine",
      created_at: base.created_at,
      shots_brewed: 0,
      profile: profileDocument(base.label),
    },
    ...rest,
  };
}

export function boardView(overrides: Partial<BoardView> = {}): BoardView {
  return {
    adopted: true,
    writes_enabled: true,
    machine_source: "mirror",
    rows: [boardRowView()],
    pending_removals: [],
    actions: [],
    reports: [],
    paused: null,
    pause_recorded: false,
    resume_preview: null,
    proposals: [],
    ...overrides,
  };
}
