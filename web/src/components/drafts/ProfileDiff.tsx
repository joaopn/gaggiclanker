import type { ReactNode } from "react";

/**
 * What a draft changed, per phase and per field.
 *
 * A JSON diff would be shorter to write and useless to read: the question a
 * person has in front of this is "what will the machine do differently", and
 * the answer is spelled in the profile's own vocabulary — a phase's name, its
 * pump setpoint, its stop conditions — not in object paths.
 *
 * Everything is flattened to `phase 2 · pump.pressure` first, which makes the
 * comparison a set operation rather than a recursive walk, and which is also
 * how `profile_patch` and the safety policy already address a field. One
 * address format across the feature means a clamp, a patch and a diff all point
 * at the same place.
 */

type Json = Record<string, unknown>;

export type FieldChange = {
  path: string;
  label: string;
  before: string | null;
  after: string | null;
};

/** Fields shown at the top, outside any phase, in the order a person reads them. */
const TOP_LEVEL = ["label", "type", "description", "temperature", "utility"] as const;

/** Per-phase fields, in the order the firmware runs them. */
const PHASE_FIELDS = [
  "name",
  "phase",
  "valve",
  "duration",
  "temperature",
  "pump",
  "transition",
  "targets",
] as const;

function render(value: unknown): string | null {
  if (value === undefined || value === null) return null;
  if (typeof value === "boolean") return value ? "yes" : "no";
  if (typeof value === "number") return String(value);
  if (typeof value === "string") return value;
  if (Array.isArray(value)) {
    if (value.length === 0) return "none";
    return value.map((item) => renderTarget(item)).join(", ");
  }
  const object = value as Json;
  if ("target" in object) {
    // The advanced pump form: name the setpoint first, because that is what the
    // phase is actually driving, and the other number is only a limit.
    const target = String(object.target);
    const setpoint = target === "pressure" ? object.pressure : object.flow;
    const limit = target === "pressure" ? object.flow : object.pressure;
    const unit = target === "pressure" ? "bar" : "ml/s";
    const other = target === "pressure" ? "flow limit" : "pressure limit";
    return `${target} ${setpoint}${unit === "bar" ? " bar" : " ml/s"}${
      limit ? `, ${other} ${limit}` : ""
    }`;
  }
  if ("type" in object && "duration" in object) {
    const adaptive = object.adaptive ? ", adaptive" : "";
    return `${object.type} over ${object.duration}s${adaptive}`;
  }
  return JSON.stringify(object);
}

function renderTarget(item: unknown): string {
  if (typeof item !== "object" || item === null) return String(item);
  const target = item as Json;
  const operator = target.operator === "lte" ? "≤" : "≥";
  return `${target.type} ${operator} ${target.value}`;
}

/** `28` and `28.0` are the same instruction; so are `[]` and absent. */
function same(a: unknown, b: unknown): boolean {
  return JSON.stringify(normalise(a)) === JSON.stringify(normalise(b));
}

function normalise(value: unknown): unknown {
  if (typeof value === "number") return Number.isInteger(value) ? value : value;
  if (Array.isArray(value)) return value.length === 0 ? null : value.map(normalise);
  if (value && typeof value === "object") {
    const out: Json = {};
    for (const [key, nested] of Object.entries(value as Json)) {
      if (nested === null || nested === undefined) continue;
      out[key] = normalise(nested);
    }
    return out;
  }
  return value === undefined ? null : value;
}

export function diffProfiles(base: Json | null, draft: Json | null): FieldChange[] {
  if (!base || !draft) return [];
  const changes: FieldChange[] = [];

  for (const field of TOP_LEVEL) {
    if (!same(base[field], draft[field])) {
      changes.push({
        path: field,
        label: field,
        before: render(base[field]),
        after: render(draft[field]),
      });
    }
  }

  const basePhases = Array.isArray(base.phases) ? (base.phases as Json[]) : [];
  const draftPhases = Array.isArray(draft.phases) ? (draft.phases as Json[]) : [];
  const count = Math.max(basePhases.length, draftPhases.length);
  for (let index = 0; index < count; index += 1) {
    const before = basePhases[index];
    const after = draftPhases[index];
    const name = String((after ?? before)?.name ?? `phase ${index + 1}`);
    if (!before) {
      changes.push({
        path: `phases[${index}]`,
        label: `phase ${index + 1} · ${name}`,
        before: null,
        after: "added",
      });
      continue;
    }
    if (!after) {
      changes.push({
        path: `phases[${index}]`,
        label: `phase ${index + 1} · ${name}`,
        before: "removed",
        after: null,
      });
      continue;
    }
    for (const field of PHASE_FIELDS) {
      if (same(before[field], after[field])) continue;
      changes.push({
        path: `phases[${index}].${field}`,
        label: `phase ${index + 1} · ${name} · ${field}`,
        before: render(before[field]),
        after: render(after[field]),
      });
    }
  }

  return changes;
}

export function ProfileDiff({
  base,
  draft,
  empty,
}: {
  base: Json | null;
  draft: Json | null;
  /** What to say when the two documents are identical. */
  empty?: ReactNode;
}) {
  const changes = diffProfiles(base, draft);
  if (changes.length === 0) {
    return (
      <p className="text-muted-foreground text-sm" data-testid="profile-diff-empty">
        {empty ?? "Nothing changed. This draft would put the same profile on the machine."}
      </p>
    );
  }
  return (
    <ul className="space-y-1" data-testid="profile-diff">
      {changes.map((change) => (
        <li key={change.path} className="flex flex-wrap items-baseline gap-x-2 text-sm">
          <span className="font-mono text-muted-foreground text-xs">{change.label}</span>
          <span className="text-muted-foreground line-through">{change.before ?? "—"}</span>
          <span aria-hidden="true">→</span>
          <span className="font-medium">{change.after ?? "—"}</span>
        </li>
      ))}
    </ul>
  );
}
