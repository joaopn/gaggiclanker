import type { ProfileDraft, SignatureRequest, SignatureRequestChange } from "@/api/types";

/** One replacement, as the Regio case reads: a floor written under the old setpoint. */
export function requestChange(
  overrides: Partial<SignatureRequestChange> = {},
): SignatureRequestChange {
  return {
    kind: "replace",
    reason: "93 was a floor 2 °C under the old 95 °C setpoint; every shot now sags under it.",
    expectation_id: 73,
    override_id: null,
    tier: "important",
    phase: "Hold",
    old_sentence: "lowest value of temperature in the Hold, at least 93 °C",
    new_sentence: "lowest value of temperature in the Hold, at least 91.5 °C",
    old_limit_text: "at least 93 °C",
    new_limit_text: "at least 91.5 °C",
    old_tier: "important",
    new_tier: "important",
    rejected_reason: "",
    rejected_at: null,
    written_expectation_id: null,
    written_override_id: null,
    old_preview: null,
    old_expectation_id: null,
    recipe_version_label: "",
    preview: { shots: 9, failing_now: 9, failing_after: 0 },
    summary: "lowest value of temperature in the Hold, at least 91.5 °C",
    stays_in_force: [
      {
        expectation_id: 77,
        words: "lowest value of temperature in the Hold, at least 91 °C",
      },
    ],
    ...overrides,
  };
}

/** A waiting request of thread 14 for profile version 27. */
export function signatureRequest(overrides: Partial<SignatureRequest> = {}): SignatureRequest {
  return {
    id: 4,
    status: "waiting",
    stale_reason: "",
    reason: "Every shot sags under the floor by about a degree.",
    answer_reason: "",
    created_at: "2026-11-01T09:00:00.000Z",
    answered_at: null,
    profile_version_id: 27,
    profile_label: "Regio Cappuccino",
    version_label: "v3",
    thread_id: 14,
    draft_id: null,
    readable: true,
    loosens_open_prediction: null,
    changes: [requestChange()],
    ...overrides,
  };
}

/** A draft whose patch lowered the temperature, with what it touches. */
export function draftWithFlags(overrides: Partial<ProfileDraft> = {}): ProfileDraft {
  return {
    affected_expectations: [
      {
        expectation_id: 73,
        sentence: "lowest value of temperature in the Hold, at least 93 °C",
        cause: "the temperature changed from 95 to 93 °C",
        revised: true,
        revision: "lowest value of temperature in the Hold, at least 91.5 °C",
      },
      {
        expectation_id: 80,
        sentence: "highest value of temperature in the Hold, at most 96 °C",
        cause: "the temperature changed from 95 to 93 °C",
        revised: false,
        revision: null,
      },
    ],
    signature_request: null,
    ...overrides,
  } as ProfileDraft;
}
