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
 * What ends the shot: how the **last** phase ends ("ends at 36 g", "ends on pressure ≥ 9", or
 * "ends after 28 s" when it has no stop). An earlier phase's stop only ends that phase.
 */
export function endsWhen(profile: Json): string {
  const phases = Array.isArray(profile.phases) ? (profile.phases as Json[]) : [];
  const last = phases[phases.length - 1];
  if (!last) return "no stop condition";
  const targets = targetsOf({ phases: [last] });
  const volume = targets.filter((t) => t.type === "volumetric");
  if (volume.length > 0) return `ends at ${Math.max(...volume.map((t) => t.value))} g`;
  const first = targets[0];
  if (first) return `ends on ${first.type} ${first.operator} ${first.value}`;
  return typeof last.duration === "number" && last.duration > 0
    ? `ends after ${last.duration} s`
    : "no stop condition";
}

/** "standard · 3 phases · ends at 36 g". */
export function profileHeadline(profile: Json): string {
  const phases = Array.isArray(profile.phases) ? profile.phases.length : 0;
  const type = typeof profile.type === "string" ? profile.type : "profile";
  return `${type} · ${phases} ${phases === 1 ? "phase" : "phases"} · ${endsWhen(profile)}`;
}
