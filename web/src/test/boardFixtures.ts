import type {
  BoardAction,
  BoardRow,
  BoardRowView,
  BoardView,
  ConflictView,
  DraftLanding,
  ListedVersion,
  ProfileVersionsView,
} from "@/api/types";

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

/** A version of a profile, as the dropdown lists it. */
export function listedVersion(overrides: Partial<ListedVersion> = {}): ListedVersion {
  const id = overrides.version_id ?? 7;
  return {
    version_id: id,
    short_hash: `hash${String(id).padStart(4, "0")}`,
    label: "9 Bar Espresso",
    type: "standard",
    created_at: "2026-03-01T00:00:00.000Z",
    added_at: "2026-03-01T00:00:00.000Z",
    source: "machine",
    is_active: false,
    is_on_machine: false,
    did_not_verify: false,
    shots_brewed: 0,
    sets_brewing: [],
    profile: profileDocument(),
    previous_version_id: null,
    ...overrides,
  };
}

export function versionsView(
  versions: ListedVersion[],
  overrides: Partial<ProfileVersionsView> = {},
): ProfileVersionsView {
  const active = versions.find((v) => v.is_active) ?? versions[0];
  return {
    row_id: 1,
    label: "9 Bar Espresso",
    on_machine: true,
    active_version_id: active?.version_id ?? 7,
    versions,
    proposed: [],
    ...overrides,
  };
}

/** The same profile with the pump at another pressure (and optionally another temperature). */
export function profileWith(pressure: number, temperature = 93): Record<string, unknown> {
  const base = profileDocument();
  const phase = (base.phases as Record<string, unknown>[])[0] as Record<string, unknown>;
  return {
    ...base,
    temperature,
    phases: [{ ...phase, pump: { target: "pressure", pressure, flow: 0 } }],
  };
}

export function conflictView(overrides: Partial<ConflictView> = {}): ConflictView {
  return {
    row_id: 1,
    label: "9 Bar Espresso",
    machine: {
      device_id: "9bar",
      version_id: 31,
      content_hash: "feedface00",
      short_hash: "feedface",
    },
    machine_profile: profileWith(7),
    app_version_id: 7,
    app_short_hash: "abcdef01",
    app_profile: profileWith(9),
    ...overrides,
  };
}

/** What making a draft active would do, with nothing in the way. */
export function landing(overrides: Partial<DraftLanding> = {}, rowId: number | null = 1) {
  return {
    draft_id: 1,
    already_on_board_label: null,
    plain: { row_id: rowId, row_label: "9 Bar Espresso", holds_newer_draft: false },
    for_set: null,
    ...overrides,
  } as DraftLanding;
}
