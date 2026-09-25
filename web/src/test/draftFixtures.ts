import type { ProfileDraft, ProfileDraftDetail } from "@/api/types";

/**
 * Factories for the draft queue's tests.
 *
 * The profiles are the firmware's own 9 Bar, because every assertion about the
 * diff is more convincing against a document somebody actually brews with than
 * against `{"a": 1}`.
 */

export function baseProfile(): Record<string, unknown> {
  return {
    label: "9 Bar Espresso",
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

export function draftProfile(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  const phases = [
    {
      ...(baseProfile().phases as Record<string, unknown>[])[0],
      pump: { target: "pressure", pressure: 8, flow: 0 },
    },
  ];
  return { ...baseProfile(), label: "9 Bar Espresso [AI]", phases, ...overrides };
}

export function draft(overrides: Partial<ProfileDraft> = {}): ProfileDraft {
  return {
    id: 1,
    base_version_id: 7,
    draft_version_id: 8,
    source_analysis_id: 3,
    source_suggestion_id: null,
    parent_draft_id: null,
    base_device_profile_id: "9bar",
    base_is_current: true,
    set_id: null,
    set_name: null,
    prediction: "",
    compares_to_version_id: null,
    compares_to_version_no: null,
    set_next_version_no: null,
    recorded_version_no: null,
    change_summary: "Dropped the peak to 8 bar.",
    stop_condition_changes: [],
    clamp_changes: [],
    notes: "",
    status: "draft",
    acknowledged_stop_changes: false,
    pushed_device_profile_id: null,
    verification: null,
    error: null,
    created_at: "2026-03-01T09:00:00.000Z",
    updated_at: "2026-03-01T09:00:00.000Z",
    base_label: "9 Bar Espresso",
    draft_label: "9 Bar Espresso [AI]",
    ...overrides,
  };
}

export function draftDetail(overrides: Partial<ProfileDraftDetail> = {}): ProfileDraftDetail {
  return {
    draft: draft(),
    base_profile: baseProfile(),
    draft_profile: draftProfile(),
    ...overrides,
  };
}

/** The stop-condition change that makes a draft require an acknowledgement. */
export function yieldChange() {
  return {
    phase_index: 0,
    phase_name: "Pump",
    kind: "changed" as const,
    target_type: "volumetric",
    before: { type: "volumetric", operator: "gte", value: 36 },
    after: { type: "volumetric", operator: "gte", value: 44 },
  };
}
