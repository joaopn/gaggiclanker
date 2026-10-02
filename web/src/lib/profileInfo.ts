/**
 * What a profile document says about itself, in a line: the type, how many phases it has and
 * what ends the shot. Read from the stored document, never from a request of its own.
 */

type Json = Record<string, unknown>;

type Target = { type: string; operator: string; value: number };

function targetsOf(profile: Json): Target[] {
  const phases = Array.isArray(profile.phases) ? (profile.phases as Json[]) : [];
  return phases.flatMap((phase) =>
    Array.isArray(phase.targets)
      ? (phase.targets as Json[]).flatMap((t) =>
          typeof t.type === "string" && typeof t.value === "number"
            ? [{ type: t.type, operator: t.operator === "lte" ? "≤" : "≥", value: t.value }]
            : [],
        )
      : [],
  );
}

/**
 * The line a row shows about the stop. With a volumetric target in any phase it is the profile's
 * **target** ("target 36 g"): the firmware's `getBrewVolume`, the last such target, which is the
 * weight the machine shows and predicts overshoot against. It is not what ends the shot (that is
 * the last phase ending, which the profile summary says), so it is never worded "ends at".
 * Without a target it says how the last phase ends ("ends on pressure ≥ 9", "ends after 28 s").
 */
export function endsWhen(profile: Json): string {
  const phases = Array.isArray(profile.phases) ? (profile.phases as Json[]) : [];
  let volume: number | null = null;
  for (const phase of phases) {
    for (const target of targetsOf({ phases: [phase] })) {
      // Zero or less is skipped by the firmware, as the profile summary skips it.
      if (target.type === "volumetric" && target.value > 0) volume = target.value;
    }
  }
  if (volume !== null) return `target ${volume} g`;
  const last = phases[phases.length - 1];
  if (!last) return "no stop condition";
  const first = targetsOf({ phases: [last] })[0];
  if (first) return `ends on ${first.type} ${first.operator} ${first.value}`;
  return typeof last.duration === "number" && last.duration > 0
    ? `ends after ${last.duration} s`
    : "no stop condition";
}

/** "standard · 3 phases · target 36 g". */
export function profileHeadline(profile: Json): string {
  const phases = Array.isArray(profile.phases) ? profile.phases.length : 0;
  const type = typeof profile.type === "string" ? profile.type : "profile";
  return `${type} · ${phases} ${phases === 1 ? "phase" : "phases"} · ${endsWhen(profile)}`;
}
