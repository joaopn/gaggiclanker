import type { ShotField, ShotFieldsData } from "@/api/types";
import { SectionCard } from "@/components/layout/SectionCard";

/**
 * The shot as a whole, after its phases, and its context.
 *
 * The outcome and the timing lead, as the server groups them: how long, what
 * came out, what it was against the target, why the machine stopped. Everything
 * else the shot-wide fields hold (temperature, pressure, flow and volume,
 * weight, resistance, the firmware analyzer's values) is context, collapsed by
 * default. Which group a field is in is the catalogue's, served with it; a
 * field's words are its own `text`. Nothing is graded.
 *
 * The phases the shot never reached and the note about what its log cannot say
 * are the phase table's; they are not repeated here.
 */

const CORE_GROUPS = new Set(["Outcome", "Timing"]);
/** The ratio is the judgement's group, but it belongs beside the yield it divides. */
const CORE_KEYS = new Set(["ratio"]);
const THE_PHASE_TABLES = new Set(["phases_not_reached", "phase_log_note"]);

function Rows({ fields }: { fields: ShotField[] }) {
  return (
    <dl className="grid grid-cols-1 gap-x-6 sm:grid-cols-2">
      {fields.map((field) => (
        <div
          key={field.key}
          className="flex items-baseline justify-between gap-3 border-border/60 border-b py-1.5"
          data-testid={`shot-field-${field.key}`}
        >
          <dt className="text-sm" title={field.name}>
            {field.label}
          </dt>
          <dd className="text-right text-sm tabular-nums">{field.text}</dd>
        </div>
      ))}
    </dl>
  );
}

function wideFields(fields: ShotFieldsData): ShotField[] {
  return fields.shot.filter((field) => !THE_PHASE_TABLES.has(field.key));
}

function isCore(field: ShotField): boolean {
  return CORE_GROUPS.has(field.group) || CORE_KEYS.has(field.key);
}

export function ShotWideCard({ fields }: { fields: ShotFieldsData }) {
  const core = wideFields(fields).filter(isCore);
  if (core.length === 0) return null;
  return (
    <SectionCard title="The shot" description="Taken as a whole.">
      <Rows fields={core} />
    </SectionCard>
  );
}

/** Everything else the shot-wide fields hold, collapsed: the page puts it last. */
export function ShotContextCard({ fields }: { fields: ShotFieldsData }) {
  const context = wideFields(fields).filter((field) => !isCore(field));
  if (context.length === 0) return null;
  const groups = [...new Set(context.map((field) => field.group))];
  return (
    <SectionCard
      title="Context"
      description="Temperature, pressure, flow and the machine's own analyzer values for the whole shot."
      collapsible
      defaultOpen={false}
    >
      <div className="space-y-3" data-testid="shot-context">
        {groups.map((group) => (
          <section key={group}>
            <h3 className="font-medium text-muted-foreground text-xs uppercase tracking-wide">
              {group}
            </h3>
            <Rows fields={context.filter((field) => field.group === group)} />
          </section>
        ))}
      </div>
    </SectionCard>
  );
}
