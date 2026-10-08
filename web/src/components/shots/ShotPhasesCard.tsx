import { useRef } from "react";
import type { ShotField, ShotFieldsData, ShotPhaseFields } from "@/api/types";
import { SectionCard } from "@/components/layout/SectionCard";
import { useVisibleWidth } from "@/components/shots/ShotRowPanel";
import { cn } from "@/lib/utils";

/**
 * One row per phase, from the served fields.
 *
 * Nothing here is computed or graded in the browser: every number and every
 * word is a field's own `text` from `GET /api/shots/{id}/fields`, so the page
 * and the chat read the same sentences. What this file decides is only which
 * fields share a column. A field a column does not name goes under that row's
 * "more", so every field the server serves has a reader and a new item in the
 * catalogue shows up without a change here.
 *
 * A field a machine did not record is **absent from the document**, never a
 * zero, and its line is simply not drawn. A phase the shot never reached is a
 * row of its own, marked "not reached", after the phases it ran. A phase that ended before the
 * machine logged a sample of it (`sampled: false`) is a row of its own too, in the profile's
 * order, saying why it ended and when: it has no pressure, flow or cup to draw, and a row of
 * empty cells would read as numbers that went missing.
 */

/** A column: the heading, the field that leads it and the ones listed under that. */
type Column = {
  id: string;
  heading: string;
  lead: string[];
  more: string[];
  /** A minimum width, where the default would let the cell wrap one word a line. */
  width?: string;
};

const COLUMNS: Column[] = [
  // What matters most comes first, straight after the phase's name, so a phone
  // shows it without a sideways scroll: how the phase ended, then the cup.
  { id: "ended", heading: "Ended by", lead: ["phase_ended_by"], more: [], width: "min-w-[5.5rem]" },
  // The cup is the line a lever shot is read by: where it stood when the phase
  // ended, and, when the shot is filed under a version with a target, what share
  // of that target it was.
  {
    id: "cup",
    heading: "Cup at end",
    lead: ["phase_cup_end"],
    more: ["phase_cup_share", "phase_cup_gained"],
    width: "min-w-[6.5rem]",
  },
  {
    id: "timing",
    heading: "Start",
    lead: ["phase_start"],
    more: ["phase_duration"],
  },
  // What reached the cup comes before everything the machine only estimates or measures
  // at the pump: it is the flow the shot is about. Absent without a scale, as before.
  {
    id: "cup-flow",
    heading: "Cup flow",
    lead: ["phase_scale_flow"],
    more: ["phase_scale_flow_peak"],
  },
  {
    id: "pressure",
    heading: "Pressure",
    lead: ["phase_pressure"],
    more: ["phase_pressure_peak", "phase_pressure_end", "phase_pressure_adherence"],
  },
  {
    id: "puck-flow",
    heading: "Puck flow",
    lead: ["phase_flow"],
    more: ["phase_flow_peak", "phase_volume", "phase_water", "phase_flow_error"],
  },
  {
    id: "temperature",
    heading: "Temperature",
    lead: ["phase_temperature"],
    more: ["phase_temperature_min", "phase_temperature_target"],
  },
  {
    id: "resistance",
    heading: "Resistance",
    lead: ["phase_resistance"],
    more: ["phase_resistance_slope"],
  },
  // The machine's own numbers, in its units, as its shot analyzer shows them.
  {
    id: "firmware",
    heading: "Firmware analyzer",
    lead: [],
    more: ["phase_machine_resistance", "phase_liquid_resistance"],
  },
];

/** Keys the row's first cell already says: they are not repeated under "more". */
const IDENTITY = new Set(["phase_name", "phase_type"]);

const SHOWN = new Set([...IDENTITY, ...COLUMNS.flatMap((c) => [...c.lead, ...c.more])]);

function byKey(phase: ShotPhaseFields): Map<string, ShotField> {
  return new Map(phase.fields.map((field) => [field.key, field]));
}

function notReached(fields: ShotFieldsData): Array<{ number: number | null; name: string }> {
  const field = fields.shot.find((item) => item.key === "phases_not_reached");
  if (!field || !Array.isArray(field.value)) return [];
  return field.value.flatMap((entry) =>
    entry && typeof entry === "object" && !Array.isArray(entry) && typeof entry.name === "string"
      ? [
          {
            number: typeof entry.phase_number === "number" ? entry.phase_number : null,
            name: entry.name,
          },
        ]
      : [],
  );
}

/** A secondary line: what it is, then the number. */
function Line({ field }: { field: ShotField }) {
  return (
    <span className="block text-muted-foreground text-xs" data-testid={`phase-${field.key}`}>
      {field.label} <span className="text-foreground tabular-nums">{field.text}</span>
    </span>
  );
}

function Cell({ column, fields }: { column: Column; fields: Map<string, ShotField> }) {
  const lead = column.lead.flatMap((key) => fields.get(key) ?? []);
  const more = column.more.flatMap((key) => fields.get(key) ?? []);
  if (lead.length === 0 && more.length === 0) {
    return <span className="text-muted-foreground">—</span>;
  }
  return (
    <>
      {lead.map((field) => (
        <span
          key={field.key}
          className="block tabular-nums"
          data-testid={`phase-${field.key}`}
          title={field.name}
        >
          {field.text}
        </span>
      ))}
      {more.map((field) => (
        <Line key={field.key} field={field} />
      ))}
    </>
  );
}

/**
 * A phase with no samples: its name, then why it ended and when, in a sentence that takes the
 * visible width of the table's scroll box. The row spans every column, and the table is wider
 * than a phone, so the sentence sits in a `sticky` block the width of what is on screen: left
 * to the row's own width it would run off the edge and need a sideways scroll to be read.
 */
function UnsampledRow({
  phase,
  span,
  width,
}: {
  phase: ShotPhaseFields;
  span: number;
  width: number | null;
}) {
  const fields = byKey(phase);
  const ended = fields.get("phase_ended_by");
  const start = fields.get("phase_start");
  return (
    <tr
      className="border-border border-b text-muted-foreground align-top last:border-0"
      data-testid="phase-unsampled"
    >
      <td colSpan={span} className="py-2">
        <div
          className="sticky left-0 whitespace-normal break-words pr-4"
          style={{ width: width ?? undefined, maxWidth: "100%" }}
          data-testid="phase-unsampled-text"
        >
          <span className="font-medium text-foreground">{phase.name}</span>{" "}
          <span className="italic">
            {ended ? ended.text : "ended before the first sample"}
            {start ? ` (at ${start.text})` : null}
          </span>
        </div>
      </td>
    </tr>
  );
}

function PhaseRow({ phase, columns }: { phase: ShotPhaseFields; columns: Column[] }) {
  const fields = byKey(phase);
  const type = fields.get("phase_type");
  const rest = phase.fields.filter((field) => !SHOWN.has(field.key));
  return (
    <tr className="border-border border-b align-top last:border-0" data-testid="phase-row">
      <td className="max-w-24 py-2 pr-3 sm:max-w-40 sm:pr-4">
        <span className="block truncate font-medium" title={phase.name}>
          {phase.name}
        </span>
        {type ? (
          <span className="block truncate text-muted-foreground text-xs">{type.text}</span>
        ) : null}
      </td>
      {columns.map((column) => (
        <td
          key={column.id}
          className={cn("py-2 pr-4 text-right text-sm", column.width ?? "min-w-36")}
          data-testid={`phase-cell-${column.id}`}
        >
          <Cell column={column} fields={fields} />
        </td>
      ))}
      <td className="whitespace-nowrap py-2 pr-4 text-right text-xs" data-testid="phase-more">
        {rest.length > 0 ? (
          <details>
            <summary className="cursor-pointer text-muted-foreground">{rest.length} more</summary>
            <span className="mt-1 block space-y-0.5">
              {rest.map((field) => (
                <Line key={field.key} field={field} />
              ))}
            </span>
          </details>
        ) : null}
      </td>
    </tr>
  );
}

/** The columns the shot has anything for: a machine with no scale has no cup-flow column. */
function usedColumns(phases: ShotPhaseFields[]): Column[] {
  return COLUMNS.filter((column) =>
    phases.some(
      (phase) =>
        phase.sampled &&
        phase.fields.some(
          (field) => column.lead.includes(field.key) || column.more.includes(field.key),
        ),
    ),
  );
}

export function ShotPhasesCard({ fields }: { fields: ShotFieldsData }) {
  const scroll = useRef<HTMLDivElement>(null);
  const visible = useVisibleWidth(scroll);
  const unreached = notReached(fields);
  const note = fields.shot.find((field) => field.key === "phase_log_note");
  if (fields.phases.length === 0 && unreached.length === 0 && !note) return null;
  const columns = usedColumns(fields.phases);
  const span = columns.length + 2;
  return (
    <SectionCard
      title="Phases"
      description="One row per phase of the shot, from the header's own transition table, in order. The cup at the end of each phase is what a lever shot is read by."
    >
      {note ? (
        <p className="mb-2 text-muted-foreground text-sm" data-testid="phase-log-note">
          {note.text}
        </p>
      ) : null}
      {fields.phases.length > 0 || unreached.length > 0 ? (
        <div ref={scroll} className="overflow-x-auto">
          <table className="w-full border-collapse text-left text-sm">
            <thead className="border-border border-b text-muted-foreground text-xs uppercase tracking-wide">
              <tr>
                <th className="py-2 pr-4 font-medium">Phase</th>
                {columns.map((column) => (
                  <th key={column.id} className="py-2 pr-4 text-right font-medium">
                    {column.heading}
                  </th>
                ))}
                <th className="whitespace-nowrap py-2 pr-4 text-right font-medium">More</th>
              </tr>
            </thead>
            <tbody>
              {fields.phases.map((phase) =>
                phase.sampled ? (
                  <PhaseRow key={`${phase.number}-${phase.name}`} phase={phase} columns={columns} />
                ) : (
                  <UnsampledRow
                    key={`${phase.number}-${phase.name}`}
                    phase={phase}
                    span={span}
                    width={visible}
                  />
                ),
              )}
              {unreached.map(({ number, name }) => (
                <tr
                  key={number ?? name}
                  className="border-border border-b text-muted-foreground last:border-0"
                  data-testid="phase-not-reached"
                >
                  <td className="max-w-24 py-2 pr-3 sm:max-w-40 sm:pr-4">
                    <span className="block truncate font-medium" title={name}>
                      {name}
                    </span>
                  </td>
                  <td className="py-2 pr-4 text-left italic" colSpan={span - 1}>
                    not reached
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </SectionCard>
  );
}
