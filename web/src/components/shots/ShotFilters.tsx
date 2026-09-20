import { ListFilter, RotateCcw, X } from "lucide-react";
import { useId, useState } from "react";
import type { ProfileVersionSummary, SetRow } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { activeFilterCount, DEFAULT_FILTERS, type ShotFilterState } from "@/lib/shotFilters";
import { SCORE_BANDS, type ScoreBandValue } from "@/lib/shots";
import { cn } from "@/lib/utils";

/**
 * The filters, behind one button.
 *
 * They used to be a bar of eight dropdowns across the top of the archive,
 * which meant the first thing anybody saw on the front page was the controls
 * rather than their shots — and eight controls of which seven are usually at
 * "any" is mostly a wall of the word "any". Behind a button with a count on it,
 * the page says what matters at a glance: how many filters are on, and nothing
 * else until you go looking.
 *
 * Inside, native `<select>` and `<input type="date">` rather than the shadcn
 * primitives used elsewhere. Three reasons, in order of weight: these are
 * exactly the controls the platform already does well; radix's Select renders
 * into a portal driven by pointer events, which turns "pick a profile" into a
 * flaky test; and a date picker built out of a popover and a calendar is a lot
 * of dependency for a field the browser ships.
 *
 * Sorting is not here. It is on the column headers, where the thing being
 * sorted is — though it still travels in the URL with everything else.
 */

const FIELD = cn(
  "h-8 w-full rounded-md border border-input bg-background px-2 text-sm",
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
);

export function ShotFilters({
  value,
  onChange,
  versions,
  sets = [],
  versionLabel = null,
}: {
  value: ShotFilterState;
  onChange: (next: ShotFilterState) => void;
  versions: ProfileVersionSummary[];
  sets?: SetRow[];
  /**
   * How the Set-version filter reads, when one is on: "Guji on the Niche v5".
   *
   * Passed in rather than looked up here, because the version *number* is not
   * on the Set row — the page that already holds the Set's detail is the cheap
   * place to find it.
   */
  versionLabel?: string | null;
}) {
  const [open, setOpen] = useState(false);
  const ids = {
    from: useId(),
    to: useId(),
    profile: useId(),
    set: useId(),
    score: useId(),
    rating: useId(),
    source: useId(),
    quarantined: useId(),
  };

  function set<K extends keyof ShotFilterState>(key: K, next: ShotFilterState[K]) {
    // Changing or clearing the Set takes its version with it: a version id
    // belongs to one Set, and carrying it onto another would filter the list
    // down to nothing for no visible reason.
    const withSet = key === "set" ? { version: "" } : {};
    onChange({ ...value, [key]: next, ...withSet });
  }

  const active = activeFilterCount(value);

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button variant="outline" size="sm" data-testid="filters-button">
          <ListFilter className="size-3.5" aria-hidden="true" />
          Filters
          {active > 0 ? (
            <Badge variant="secondary" className="ml-1 px-1.5" data-testid="filters-count">
              {active}
            </Badge>
          ) : null}
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-80 space-y-3" data-testid="shot-filters" aria-label="Filters">
        <div className="grid grid-cols-2 gap-3">
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
        </div>
        <Field id={ids.profile} label="Profile">
          <select
            id={ids.profile}
            className={FIELD}
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
            className={FIELD}
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
          {/* One version of that Set, arrived at from the Set page's log. There
              is no control to *pick* a version here — the log is where a
              version means something — so this is a removable statement of
              what is on rather than a second select nobody would use. */}
          {versionLabel ? (
            <div
              data-testid="version-filter"
              className="mt-1 flex min-w-0 items-center gap-1 rounded-md bg-muted px-2 py-1"
            >
              <span className="min-w-0 flex-1 truncate text-xs" title={versionLabel}>
                {versionLabel}
              </span>
              <button
                type="button"
                aria-label={`Show every version of ${versionLabel.replace(/ v\d+$/, "")}`}
                onClick={() => set("version", "")}
                className="shrink-0 rounded-sm p-0.5 text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <X className="size-3.5" aria-hidden="true" />
              </button>
            </div>
          ) : null}
        </Field>
        <div className="grid grid-cols-2 gap-3">
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
        </div>
        <div className="flex justify-end">
          <Button
            variant="ghost"
            size="sm"
            disabled={active === 0}
            onClick={() =>
              // The sort survives: it is not one of the filters this button
              // clears, and somebody who chose "worst first" did not ask for
              // it to be undone by a button labelled "Clear all".
              onChange({ ...DEFAULT_FILTERS, sort: value.sort, order: value.order })
            }
          >
            <RotateCcw className="size-3.5" aria-hidden="true" />
            Clear all
          </Button>
        </div>
      </PopoverContent>
    </Popover>
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
