import { renderValue } from "@/components/drafts/ProfileDiff";

type Json = Record<string, unknown>;

/** The firmware's default (an instant step of no length) says nothing worth a line. */
function hasTransition(transition: unknown): boolean {
  if (typeof transition !== "object" || transition === null) return false;
  const { type, duration } = transition as Json;
  return !(type === "instant" && !duration);
}

/**
 * The stop conditions the firmware acts on, each spelled as the diff spells it. A volumetric
 * target of zero or less is skipped by the firmware (`Phase::isFinished`), so it is not shown.
 */
function targetsOf(phase: Json): string[] {
  if (!Array.isArray(phase.targets)) return [];
  return phase.targets
    .filter((target) => {
      const { type, value } = (target ?? {}) as Json;
      return !(type === "volumetric" && !(typeof value === "number" && value > 0));
    })
    .map((target) => renderValue([target]))
    .filter((text): text is string => text !== null);
}

/** A simple pump is a power percentage (`getPumpValue`); an advanced one names its setpoint. */
function pumpOf(pump: unknown): string | null {
  if (typeof pump === "number") return `pump at ${pump}%`;
  const text = renderValue(pump);
  return text === null ? null : `pump ${text}`;
}

/** A phase's length: for a standard profile with a volumetric target, only when volume is unmeasured. */
function durationOf(profile: Json, phase: Json): string | null {
  if (typeof phase.duration !== "number") return null;
  const volumetric = targetsOf(phase).some((text) => text.startsWith("volumetric"));
  return profile.type === "standard" && volumetric
    ? `${phase.duration} s when volume is not measured`
    : `${phase.duration} s`;
}

/**
 * How the shot ends, from the firmware's own rules (`BrewProcess::progress`, `Phase::isFinished`):
 * a phase ends when any one of its targets is reached or, failing that, when its duration is up;
 * the shot ends when the last phase ends. There is no stop condition on the profile itself. A
 * phase of a standard profile that has a volumetric target ignores its duration while the machine
 * measures volume (its targets, of any kind, still end it); the duration ends it otherwise.
 */
function shotEnds(profile: Json, phases: Json[]): string {
  const last = phases[phases.length - 1];
  if (last === undefined) return "Shot ends when the last phase ends.";
  const targets = targetsOf(last);
  const after = typeof last.duration === "number" ? `${last.duration} s` : null;
  const volumetric = targets.some((text) => text.startsWith("volumetric"));
  const parts = targets.length > 0 ? [targets.join(" or ")] : [];
  if (after !== null) {
    parts.push(
      profile.type === "standard" && volumetric
        ? `after ${after} when volume is not measured`
        : `after ${after}`,
    );
  }
  return `Shot ends when the last phase ends: ${parts.join(", or ")}.`;
}

/**
 * A profile read as it is, not as a change: its type and temperature, how the shot ends, and each
 * phase (name, length, pump, transition, and what ends that phase) in the order the machine runs
 * them. Used where a draft is a profile designed from scratch and there is nothing to diff it
 * against; the same component serves anywhere a version's information is shown.
 *
 * Every value is spelled by the diff's own `renderValue`, so a phase reads here as it reads in a
 * diff of the same profile.
 */
export function ProfileSummary({ profile }: { profile: Json | null }) {
  if (profile === null) {
    return <p className="text-muted-foreground text-sm">The profile is not available.</p>;
  }
  const phases = Array.isArray(profile.phases) ? (profile.phases as Json[]) : [];
  const facts = [
    profile.type ? `type ${String(profile.type)}` : null,
    typeof profile.temperature === "number" && profile.temperature > 0
      ? `${profile.temperature} °C`
      : null,
    `${phases.length} ${phases.length === 1 ? "phase" : "phases"}`,
  ].filter((fact): fact is string => fact !== null);

  return (
    <div data-testid="profile-summary" className="space-y-2">
      <p className="text-sm">{facts.join(" · ")}</p>
      {profile.description ? (
        <p className="text-muted-foreground text-sm">{String(profile.description)}</p>
      ) : null}
      <p className="text-sm" data-testid="profile-summary-ends">
        {shotEnds(profile, phases)}
      </p>
      <ol className="space-y-1">
        {phases.map((phase, index) => {
          const targets = targetsOf(phase);
          const details = [
            durationOf(profile, phase),
            pumpOf(phase.pump),
            hasTransition(phase.transition) ? `transition ${renderValue(phase.transition)}` : null,
            targets.length > 0 ? `ends when ${targets.join(" or ")}` : null,
          ].filter((text): text is string => text !== null);
          return (
            // The phases of one document have no id of their own; their order is their identity.
            // biome-ignore lint/suspicious/noArrayIndexKey: see above
            <li key={index} className="text-sm" data-testid="profile-summary-phase">
              <span className="font-medium">{String(phase.name ?? `Phase ${index + 1}`)}</span>
              {details.length > 0 ? (
                <span className="text-muted-foreground"> — {details.join(" · ")}</span>
              ) : null}
            </li>
          );
        })}
      </ol>
    </div>
  );
}
