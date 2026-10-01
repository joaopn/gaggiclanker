import type { ReactNode } from "react";
import type { DeviceShotNotes } from "@/api/types";
import { SectionCard } from "@/components/layout/SectionCard";
import { RatingStars } from "@/components/shots/RatingStars";

/**
 * The device's own notes, mirrored.
 *
 * Read-only wherever it is shown: this is what was typed on the machine, read
 * in by a sync. This box never writes a notes card back. Shared by the
 * shot page and the shots list's open row, so the two cannot show a different
 * card for the same shot.
 */
export function DeviceNotesCard({
  notes,
  description = "What was typed on the machine's own notes card for this shot, as of the last sync. Your judgement is kept here and never sent back to the machine.",
  className,
}: {
  notes: DeviceShotNotes;
  description?: ReactNode;
  className?: string;
}) {
  const entries: Array<[string, string]> = [
    ["Bean", notes.bean_type ?? ""],
    ["Dose in", notes.dose_in_g == null ? "" : `${notes.dose_in_g} g`],
    ["Dose out", notes.dose_out_g == null ? "" : `${notes.dose_out_g} g`],
    ["Ratio", notes.ratio == null ? "" : `1:${notes.ratio}`],
    ["Grind", notes.grind_setting ?? ""],
    ["Balance", notes.balance_taste ?? ""],
  ];
  return (
    <SectionCard
      title="Device notes"
      description={description}
      actions={<RatingStars rating={notes.rating ?? null} />}
      className={className}
    >
      <dl className="grid grid-cols-2 gap-x-4 gap-y-2 sm:grid-cols-3" data-testid="device-notes">
        {entries.map(([label, value]) => (
          <div key={label}>
            <dt className="text-muted-foreground text-xs">{label}</dt>
            <dd className="text-sm">{value || "—"}</dd>
          </div>
        ))}
      </dl>
      {notes.notes ? <p className="mt-3 whitespace-pre-wrap text-sm">{notes.notes}</p> : null}
    </SectionCard>
  );
}
