import { AlertTriangle } from "lucide-react";
import { type RefObject, useRef } from "react";
import { Link } from "react-router-dom";
import type { ShotListRow } from "@/api/types";
import { SetBadge } from "@/components/sets/SetBadge";
import { RatingStars } from "@/components/shots/RatingStars";
import { ScoreBadge } from "@/components/shots/ScoreBadge";
import { ShotSparkline } from "@/components/shots/ShotSparkline";
import { Badge } from "@/components/ui/badge";
import { useVirtualRows } from "@/hooks/useVirtualRows";
import { formatGrams, formatSeconds, formatTime, profileName } from "@/lib/shots";
import { cn } from "@/lib/utils";

/**
 * The archive as a table, windowed.
 *
 * A grid rather than a `<table>`: virtualising a table means spacer `<tr>`s
 * whose height browsers treat as advisory, and a row here has to be exactly
 * `ROW_HEIGHT` for the window arithmetic to hold. The row is a link with a
 * checkbox beside it rather than a link wrapping everything, because a
 * checkbox inside an anchor is a click that means two things.
 */

/** Fixed, and enforced by the row's own style — see `useVirtualRows`. */
export const ROW_HEIGHT = 52;

const COLUMNS =
  "grid grid-cols-[10.5rem_minmax(6rem,1fr)_3.5rem_4rem_3.25rem] items-center gap-3 " +
  "md:grid-cols-[10.5rem_minmax(8rem,1fr)_6rem_4rem_4.5rem_3.25rem_5.5rem_minmax(6rem,auto)]";

export function ShotsTable({
  shots,
  selected,
  onToggleSelected,
  scrollRef,
  maxCompare,
}: {
  shots: ShotListRow[];
  selected: number[];
  onToggleSelected: (id: number) => void;
  scrollRef: RefObject<HTMLDivElement | null>;
  maxCompare: number;
}) {
  const window = useVirtualRows(shots.length, { rowHeight: ROW_HEIGHT, containerRef: scrollRef });
  const visible = shots.slice(window.start, window.end);

  return (
    <div>
      {/* The same flex-plus-grid the row uses, including a spacer the width of
          the compare checkbox. Laying the header out as a bare grid put every
          heading a checkbox to the left of its column. */}
      <div
        className={cn(
          "sticky top-0 z-10 flex items-center gap-2 border-border border-b bg-background",
          "pr-3 pl-2 py-2 text-muted-foreground text-xs uppercase tracking-wide",
        )}
      >
        <span className="size-3.5 shrink-0" aria-hidden="true" />
        <div className={cn(COLUMNS, "min-w-0 flex-1")}>
          <span>Time</span>
          <span>Profile</span>
          <span className="hidden md:inline">Curve</span>
          <span className="text-right">Time</span>
          <span className="hidden text-right md:inline">Yield</span>
          <span className="text-right">Score</span>
          <span className="hidden md:inline">Rating</span>
          <span className="hidden md:inline">Flags</span>
        </div>
      </div>
      <div data-testid="shot-rows">
        <div style={{ height: window.paddingTop }} aria-hidden="true" />
        {visible.map((shot) => (
          <ShotRow
            key={shot.id}
            shot={shot}
            selected={selected.includes(shot.id)}
            onToggleSelected={onToggleSelected}
            selectionFull={selected.length >= maxCompare}
          />
        ))}
        <div style={{ height: window.paddingBottom }} aria-hidden="true" />
      </div>
    </div>
  );
}

function ShotRow({
  shot,
  selected,
  onToggleSelected,
  selectionFull,
}: {
  shot: ShotListRow;
  selected: boolean;
  onToggleSelected: (id: number) => void;
  selectionFull: boolean;
}) {
  const rowRef = useRef<HTMLDivElement>(null);
  return (
    <div
      ref={rowRef}
      data-testid="shot-row"
      data-shot={shot.id}
      style={{ height: ROW_HEIGHT }}
      className="flex items-center gap-2 border-border border-b pr-3 pl-2 last:border-0 hover:bg-muted/40"
    >
      <input
        type="checkbox"
        className="size-3.5 shrink-0 accent-primary"
        checked={selected}
        // Three is the limit the compare drawer draws; a fourth line makes an
        // overlay unreadable rather than more informative.
        disabled={!selected && selectionFull}
        onChange={() => onToggleSelected(shot.id)}
        aria-label={`Compare shot ${shot.device_id}`}
      />
      <Link to={`/shots/${shot.id}`} className={cn(COLUMNS, "min-w-0 flex-1 py-1")}>
        <span className="truncate whitespace-nowrap text-sm tabular-nums">
          {formatTime(shot.started_at)}
        </span>
        <span className="min-w-0 truncate text-sm">{profileName(shot)}</span>
        <span className="hidden md:inline">
          {shot.quarantined ? null : <ShotSparkline shotId={shot.id} />}
        </span>
        <span className="text-right text-sm tabular-nums">{formatSeconds(shot.duration_ms)}</span>
        <span className="hidden text-right text-sm tabular-nums md:inline">
          {formatGrams(shot.volume_g)}
        </span>
        <span className="text-right">
          <ScoreBadge score={shot.execution_score ?? null} />
        </span>
        <span className="hidden md:inline">
          <RatingStars rating={shot.rating ?? shot.index_rating ?? null} />
        </span>
        <span className="hidden flex-wrap gap-1 md:flex">
          <ShotFlags shot={shot} />
        </span>
      </Link>
    </div>
  );
}

/**
 * The badges a row can carry, including the one that is not filled in yet.
 *
 * The Set badge is the important one: "needs a Set" is a state with a button
 * behind it, not an absence, so it is rendered rather than left out. The
 * analysis state is still a dimmed placeholder — the column exists, its
 * width is already paid for, and a reader learns it is a state rather than a
 * missing feature.
 */
function ShotFlags({ shot }: { shot: ShotListRow }) {
  return (
    <>
      {shot.quarantined ? (
        <Badge variant="destructive" className="gap-1">
          <AlertTriangle className="size-3" aria-hidden="true" />
          quarantined
        </Badge>
      ) : null}
      {shot.deleted_on_device ? <Badge variant="outline">gone from machine</Badge> : null}
      {shot.incomplete ? <Badge variant="outline">incomplete</Badge> : null}
      {shot.source === "import" ? <Badge variant="secondary">imported</Badge> : null}
      <span data-testid="set-badge-slot">
        <SetBadge badge={shot.set_badge ?? null} />
      </span>
      <span
        data-testid="analysis-slot"
        className="text-[10px] text-muted-foreground/60"
        title="LLM analysis arrives later"
      >
        no analysis
      </span>
    </>
  );
}
