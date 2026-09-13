import { AlertTriangle, ArrowDown, ArrowUp, Sparkles } from "lucide-react";
import { type RefObject, useRef } from "react";
import { Link } from "react-router-dom";
import type { ShotListRow, ShotSort } from "@/api/types";
import { SetBadge } from "@/components/sets/SetBadge";
import { RatingStars } from "@/components/shots/RatingStars";
import { ScoreBadge } from "@/components/shots/ScoreBadge";
import { ShotRowEditor } from "@/components/shots/ShotRowEditor";
import { ShotSparkline } from "@/components/shots/ShotSparkline";
import { Badge } from "@/components/ui/badge";
import { usePatchJudgement } from "@/hooks/useSets";
import { useVirtualRows } from "@/hooks/useVirtualRows";
import { attempt } from "@/lib/mutations";
import { gridTemplates, type ShotColumn, type ShotColumnId } from "@/lib/shotColumns";
import { formatGrams, formatSeconds, formatTime, profileName } from "@/lib/shots";
import { cn } from "@/lib/utils";

/**
 * The archive as a table, windowed, with the columns the reader chose.
 *
 * A grid rather than a `<table>`: virtualising a table means spacer `<tr>`s
 * whose height browsers treat as advisory, and a row here has to be exactly
 * `ROW_HEIGHT` for the window arithmetic to hold. The row is a link with a
 * checkbox beside it rather than a link wrapping everything, because a
 * checkbox inside an anchor is a click that means two things.
 *
 * The grid template is computed from the visible columns rather than written
 * out, so that adding a column is one entry in `lib/shotColumns.ts` and not
 * three edits that have to agree.
 */

/** Fixed, and enforced by the row's own style — see `useVirtualRows`. */
export const ROW_HEIGHT = 52;

/**
 * The four keys the server can sort on. Everything else is a plain heading:
 * offering a header that quietly does nothing is worse than not offering it.
 */
const SORTABLE: Partial<Record<ShotColumnId, ShotSort>> = {
  time: "started_at",
  duration: "duration",
  score: "execution_score",
  rating: "rating",
};

export function ShotsTable({
  shots,
  columns,
  selected,
  onToggleSelected,
  scrollRef,
  maxCompare,
  sort,
  order,
  onSort,
}: {
  shots: ShotListRow[];
  columns: ShotColumn[];
  selected: number[];
  onToggleSelected: (id: number) => void;
  scrollRef: RefObject<HTMLDivElement | null>;
  maxCompare: number;
  sort: ShotSort;
  order: "asc" | "desc";
  onSort: (key: ShotSort) => void;
}) {
  const window = useVirtualRows(shots.length, { rowHeight: ROW_HEIGHT, containerRef: scrollRef });
  const visible = shots.slice(window.start, window.end);
  const templates = gridTemplates(columns);
  // The template has to reach the DOM as a value, not as a class: Tailwind
  // cannot generate a class for a string it has never seen, and these are
  // built at runtime from whatever the reader ticked.
  const style = {
    "--shots-grid-narrow": templates.narrow,
    "--shots-grid-wide": templates.wide,
  } as React.CSSProperties;

  return (
    <div style={style}>
      {/* The same flex-plus-grid the row uses, including a spacer the width of
          the compare checkbox. Laying the header out as a bare grid put every
          heading a checkbox to the left of its column. */}
      <div
        className={cn(
          "sticky top-0 z-10 flex items-center gap-2 border-border border-b bg-background",
          "py-1 pr-3 pl-2 text-muted-foreground text-xs uppercase tracking-wide",
        )}
      >
        <span className="size-3.5 shrink-0" aria-hidden="true" />
        <div className={cn(GRID, "min-w-0 flex-1")}>
          {columns.map((column) => (
            <HeaderCell key={column.id} column={column} sort={sort} order={order} onSort={onSort} />
          ))}
        </div>
        {/* The row editor's button sits outside the grid, so the header needs
            a spacer of the same width or every heading drifts left of its
            column on the widest breakpoint. */}
        <span className="size-7 shrink-0" aria-hidden="true" />
      </div>
      <div data-testid="shot-rows">
        <div style={{ height: window.paddingTop }} aria-hidden="true" />
        {visible.map((shot) => (
          <ShotRow
            key={shot.id}
            shot={shot}
            columns={columns}
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

/** Both templates, read off the custom properties the table sets. */
const GRID =
  "grid items-center gap-3 [grid-template-columns:var(--shots-grid-narrow)] md:[grid-template-columns:var(--shots-grid-wide)]";

function HeaderCell({
  column,
  sort,
  order,
  onSort,
}: {
  column: ShotColumn;
  sort: ShotSort;
  order: "asc" | "desc";
  onSort: (key: ShotSort) => void;
}) {
  const key = SORTABLE[column.id];
  const active = key !== undefined && key === sort;
  const className = cn(
    column.numeric && "text-right",
    column.narrowHidden && "hidden md:block",
    "min-w-0 truncate",
  );

  if (key === undefined) {
    return <span className={className}>{column.label}</span>;
  }
  return (
    // `aria-sort` goes on the header cell, not on the button: it describes the
    // column, and a screen reader announcing "sorted ascending" on the control
    // that changes the sort is announcing the wrong thing. The cell carries
    // the role explicitly because this table is a CSS grid rather than a
    // `<table>` — see the note at the top of this file — so there is no `<th>`
    // to put it on.
    // biome-ignore lint/a11y/useSemanticElements: a `<th>` cannot be a grid item outside a table.
    // biome-ignore lint/a11y/useFocusableInteractive: the button inside it is what takes focus.
    <span
      role="columnheader"
      className={className}
      aria-sort={active ? (order === "asc" ? "ascending" : "descending") : "none"}
      data-testid={`header-${column.id}`}
    >
      <button
        type="button"
        data-testid={`sort-${column.id}`}
        onClick={() => onSort(key)}
        className={cn(
          "inline-flex items-center gap-1 uppercase tracking-wide hover:text-foreground",
          column.numeric && "flex-row-reverse",
          active && "text-foreground",
        )}
      >
        {column.label}
        {active ? (
          order === "asc" ? (
            <ArrowUp className="size-3" aria-hidden="true" />
          ) : (
            <ArrowDown className="size-3" aria-hidden="true" />
          )
        ) : null}
        <span className="sr-only">
          {active
            ? `sorted ${order === "asc" ? "ascending" : "descending"}, click to reverse`
            : "click to sort by this column"}
        </span>
      </button>
    </span>
  );
}

function ShotRow({
  shot,
  columns,
  selected,
  onToggleSelected,
  selectionFull,
}: {
  shot: ShotListRow;
  columns: ShotColumn[];
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
      <Link to={`/shots/${shot.id}`} className={cn(GRID, "min-w-0 flex-1 py-1")}>
        {columns.map((column) => (
          <span
            key={column.id}
            className={cn(
              "min-w-0",
              column.numeric && "text-right",
              column.narrowHidden && "hidden md:block",
            )}
          >
            <Cell shot={shot} id={column.id} />
          </span>
        ))}
      </Link>
      <ShotRowEditor shot={shot} />
    </div>
  );
}

function Cell({ shot, id }: { shot: ShotListRow; id: ShotColumnId }) {
  switch (id) {
    case "time":
      return (
        <span className="truncate whitespace-nowrap text-sm tabular-nums">
          {formatTime(shot.started_at)}
        </span>
      );
    case "profile":
      return <span className="block truncate text-sm">{profileName(shot)}</span>;
    case "curve":
      return shot.quarantined ? null : <ShotSparkline shotId={shot.id} />;
    case "duration":
      return <span className="text-sm tabular-nums">{formatSeconds(shot.duration_ms)}</span>;
    case "yield":
      return <span className="text-sm tabular-nums">{formatGrams(shot.volume_g)}</span>;
    case "score":
      return <ScoreBadge score={shot.execution_score ?? null} />;
    case "rating":
      return <RatingCell shot={shot} />;
    case "set":
      return (
        <span data-testid="set-badge-slot">
          <SetBadge badge={shot.set_badge ?? null} />
        </span>
      );
    case "notes":
      return (
        <span className="block truncate text-muted-foreground text-xs" data-testid="notes-cell">
          {shot.judgement_notes || ""}
        </span>
      );
    case "flags":
      return (
        <span className="flex flex-wrap gap-1">
          <ShotFlags shot={shot} />
        </span>
      );
  }
}

/**
 * The stars, clickable where they sit.
 *
 * A rating is the one thing about a cup somebody records for every shot, and
 * for most shots it is the only thing — so it is a click in the list rather
 * than a page visit. The write merges into whatever verdict already exists
 * (`usePatchJudgement`), because `PUT` replaces the row and the taste tags,
 * doses, grind and decision typed on the detail page must survive a star.
 */
function RatingCell({ shot }: { shot: ShotListRow }) {
  const patch = usePatchJudgement();
  return (
    <RatingStars
      rating={ratingOf(shot)}
      label={`shot ${shot.device_id}`}
      onRate={(rating) => {
        void attempt(() => patch.mutateAsync({ shotId: shot.id, patch: { rating } }));
      }}
    />
  );
}

/**
 * The rating a row shows: this box's verdict first, then the machine's own
 * notes card, then the index's figure. The same order the server sorts and
 * filters by (`db/repos/shots.py`), so the column and the sort agree.
 */
export function ratingOf(shot: ShotListRow): number | null {
  return shot.judgement_rating ?? shot.rating ?? shot.index_rating ?? null;
}

/**
 * The badges a row can carry.
 *
 * The analysis state is a state with something to do behind it rather than an
 * absence: "not analysed" is rendered rather than left blank, because a row
 * that said nothing would read as a shot with no analysis available rather
 * than one waiting for a click. The Set badge is the same idea and has grown
 * into a column of its own.
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
      <AnalysisFlag state={shot.analysis_state} />
    </>
  );
}

/**
 * Where the newest analysis of this row got to.
 *
 * Four states, not five: an `interrupted` row — one a restart cut off — is
 * reported as `failed` by the server, because to somebody scanning a list the
 * two mean the same thing and a fifth word would only need explaining.
 */
function AnalysisFlag({ state }: { state: string }) {
  if (state === "ok") {
    return (
      <Badge variant="secondary" className="gap-1" data-testid="analysis-slot">
        <Sparkles className="size-3" aria-hidden="true" />
        analysed
      </Badge>
    );
  }
  if (state === "running") {
    return (
      <Badge variant="outline" className="gap-1" data-testid="analysis-slot">
        <Sparkles className="size-3 animate-pulse" aria-hidden="true" />
        analysing
      </Badge>
    );
  }
  if (state === "failed") {
    return (
      <Badge variant="outline" className="gap-1 text-status-warn-text" data-testid="analysis-slot">
        <AlertTriangle className="size-3" aria-hidden="true" />
        analysis failed
      </Badge>
    );
  }
  return (
    <span data-testid="analysis-slot" className="text-[10px] text-muted-foreground/60">
      not analysed
    </span>
  );
}
