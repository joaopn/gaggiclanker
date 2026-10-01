import type { BoardAction, BoardRow, BoardRowView, BoardView } from "@/api/types";

/** Factories for the profile board's tests. */

export function boardRow(overrides: Partial<BoardRow> = {}): BoardRow {
  return {
    id: 1,
    label: "9 Bar Espresso",
    current_version_id: 7,
    origin: "adopted",
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
    ...overrides,
  };
}
