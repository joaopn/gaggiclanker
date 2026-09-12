import { RotateCcw } from "lucide-react";
import { useId } from "react";
import type { ProfileVersionSummary, SetRow, ShotSort } from "@/api/types";
import { Button } from "@/components/ui/button";
import { DEFAULT_FILTERS, isDefaultFilters, type ShotFilterState } from "@/lib/shotFilters";
import { SCORE_BANDS, type ScoreBandValue } from "@/lib/shots";
import { cn } from "@/lib/utils";

/**
 * The filter bar.
 *
 * Native `<select>` and `<input type="date">` rather than the shadcn/radix
 * primitives used elsewhere. Three reasons, in order of weight: a filter bar is
 * exactly the control the platform already does well; radix's Select renders
 * into a portal driven by pointer events, which turns "pick a profile" into a
 * flaky test; and a date picker built out of a popover and a calendar is a lot
 * of dependency for a field the browser ships.
 */

const FIELD = cn(
  "h-8 rounded-md border border-input bg-background px-2 text-sm",
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
);

export function ShotFilters({
  value,
  onChange,
  versions,
  sets = [],
}: {
  value: ShotFilterState;
  onChange: (next: ShotFilterState) => void;
  versions: ProfileVersionSummary[];
  sets?: SetRow[];
}) {
  const ids = {
    from: useId(),
    to: useId(),
    profile: useId(),
    set: useId(),
    score: useId(),
    rating: useId(),
    source: useId(),
    quarantined: useId(),
    sort: useId(),
  };

  function set<K extends keyof ShotFilterState>(key: K, next: ShotFilterState[K]) {
    onChange({ ...value, [key]: next });
  }

  return (
    <div
      className="flex flex-wrap items-end gap-x-3 gap-y-2 rounded-lg border border-border bg-card/40 p-3"
      data-testid="shot-filters"
    >
      <Field id={ids.from} label="From">
        <input
          id={ids.from}
          type="date"
          className={FIELD}
          value={value.from}
          onChange={(event) => set("from", event.target.value)}
        />
      </Field>
      <Field id={ids.to} label="To">
        <input
          id={ids.to}
          type="date"
          className={FIELD}
          value={value.to}
          onChange={(event) => set("to", event.target.value)}
        />
      </Field>
      <Field id={ids.profile} label="Profile">
        <select
          id={ids.profile}
          className={cn(FIELD, "max-w-[14rem]")}
          value={value.profileVersionId}
          onChange={(event) => set("profileVersionId", event.target.value)}
        >
          <option value="">Any profile</option>
          {versions.map((version) => (
            <option key={version.id} value={String(version.id)}>
              {version.label}
            </option>
          ))}
        </select>
      </Field>
      <Field id={ids.set} label="Set">
        <select
          id={ids.set}
          className={cn(FIELD, "max-w-[14rem]")}
          value={value.set}
          onChange={(event) => set("set", event.target.value)}
        >
          <option value="">Any Set</option>
          {/* The inbox, first: it is the only option anybody comes here to
              click twice, and it is not a Set id. */}
          <option value="needs">Needs a Set</option>
          {sets.map((row) => (
            <option key={row.id} value={String(row.id)}>
              {row.name}
            </option>
          ))}
        </select>
      </Field>
      <Field id={ids.score} label="Score">
        <select
          id={ids.score}
          className={FIELD}
          value={value.scoreBand}
          onChange={(event) => set("scoreBand", event.target.value as ScoreBandValue)}
        >
          {SCORE_BANDS.map((band) => (
            <option key={band.value} value={band.value}>
              {band.label}
            </option>
          ))}
        </select>
      </Field>
      <Field id={ids.rating} label="Rating">
        <select
          id={ids.rating}
          className={FIELD}
          value={value.minRating}
          onChange={(event) => set("minRating", event.target.value)}
        >
          <option value="">Any rating</option>
          {[1, 2, 3, 4, 5].map((stars) => (
            <option key={stars} value={String(stars)}>
              {stars}+ stars
            </option>
          ))}
        </select>
      </Field>
      <Field id={ids.source} label="Source">
        <select
          id={ids.source}
          className={FIELD}
          value={value.source}
          onChange={(event) => set("source", event.target.value as ShotFilterState["source"])}
        >
          <option value="">Any source</option>
          <option value="device">From the machine</option>
          <option value="import">Imported</option>
        </select>
      </Field>
      <Field id={ids.quarantined} label="Readable">
        <select
          id={ids.quarantined}
          className={FIELD}
          value={value.quarantined}
          onChange={(event) =>
            set("quarantined", event.target.value as ShotFilterState["quarantined"])
          }
        >
          <option value="">All shots</option>
          <option value="no">Parsed</option>
          <option value="yes">Quarantined</option>
        </select>
      </Field>
      <Field id={ids.sort} label="Sort">
        <select
          id={ids.sort}
          className={FIELD}
          value={`${value.sort}:${value.order}`}
          onChange={(event) => {
            const [sort, order] = event.target.value.split(":");
            onChange({
              ...value,
              sort: sort as ShotSort,
              order: order as "asc" | "desc",
            });
          }}
        >
          <option value="started_at:desc">Newest first</option>
          <option value="started_at:asc">Oldest first</option>
          <option value="execution_score:desc">Best executed</option>
          <option value="execution_score:asc">Worst executed</option>
          <option value="duration:desc">Longest</option>
          <option value="duration:asc">Shortest</option>
          <option value="rating:desc">Highest rated</option>
        </select>
      </Field>
      {!isDefaultFilters(value) ? (
        <Button variant="ghost" size="sm" onClick={() => onChange(DEFAULT_FILTERS)}>
          <RotateCcw className="size-3.5" aria-hidden="true" />
          Clear
        </Button>
      ) : null}
    </div>
  );
}

function Field({ id, label, children }: { id: string; label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className="text-muted-foreground text-xs">
        {label}
      </label>
      {children}
    </div>
  );
}
