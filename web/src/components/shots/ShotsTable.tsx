import { AlertTriangle, ArrowDown, ArrowUp, Sparkles } from "lucide-react";
import {
  type KeyboardEvent,
  type RefObject,
  useCallback,
  useId,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import type { ShotListRow, ShotSort } from "@/api/types";
import { SetBadge } from "@/components/sets/SetBadge";
import { AnalyseCell } from "@/components/shots/AnalyseCell";
import { NeedsSetMenu } from "@/components/shots/NeedsSetMenu";
import { RatingStars } from "@/components/shots/RatingStars";
import { ScoreBadge } from "@/components/shots/ScoreBadge";
import { ShotRowEditor } from "@/components/shots/ShotRowEditor";
import { ShotRowPanel } from "@/components/shots/ShotRowPanel";
import { ShotSparkline } from "@/components/shots/ShotSparkline";
import { Badge } from "@/components/ui/badge";
import { usePatchJudgement } from "@/hooks/useSets";
import { useVirtualRows } from "@/hooks/useVirtualRows";
import { attempt } from "@/lib/mutations";
import {
  clampWidth,
  columnWidth,
  type FixedSize,
  fixedSize,
  gridTemplates,
  type ShotColumn,
  type ShotColumnId,
  type ShotWidths,
} from "@/lib/shotColumns";
import { formatGrams, formatListTime, formatSeconds, formatTime, profileName } from "@/lib/shots";
import { cn } from "@/lib/utils";

/**
 * The archive as a table, windowed, with the columns the reader chose.
 *
 * A grid rather than a `<table>`: virtualising a table means spacer `<tr>`s
 * whose height browsers treat as advisory, and a row here has to be exactly
 * `ROW_HEIGHT` for the window arithmetic to hold.
 *
 * **A row opens in place** rather than navigating: clicking it shows the curve,
 * the quick judgement and the machine's notes directly below it
 * (`ShotRowPanel`), and clicking it again closes them. One row is open at a
 * time — opening another closes the first — because the window arithmetic
 * accounts for exactly one panel, and because two open forms is two places to
 * lose half-typed input. The shot page is a link inside the panel.
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
  widths,
  onResize,
}: {
  shots: ShotListRow[];
  columns: ShotColumn[];
  /** The reader's column widths, in rem; a column with none is at its default. */
  widths: ShotWidths;
  /** A new width for one column, or `null` to put it back to its default. */
  onResize: (id: ShotColumnId, rem: number | null) => void;
  selected: number[];
  onToggleSelected: (id: number) => void;
  scrollRef: RefObject<HTMLDivElement | null>;
  maxCompare: number;
  sort: ShotSort;
  order: "asc" | "desc";
  onSort: (key: ShotSort) => void;
}) {
  const [openId, setOpenId] = useState<number | null>(null);
  const [panelHeight, setPanelHeight] = useState(0);
  const panelPrefix = useId();
  const headerRef = useRef<HTMLDivElement>(null);
  // The open shot's place in the list, looked up rather than stored: a refetch
  // can put a new shot above it, and a filter can take it out of the list
  // altogether, in which case nothing is open as far as the window is concerned.
  const openIndex = openId === null ? -1 : shots.findIndex((shot) => shot.id === openId);
  const window = useVirtualRows(shots.length, {
    rowHeight: ROW_HEIGHT,
    containerRef: scrollRef,
    expanded: openIndex >= 0 ? { index: openIndex, height: panelHeight } : null,
  });

  // Scroll to undo when one open row is swapped for another further down: the
  // panel closing above the clicked row pulls it up by the panel's height, and
  // the row somebody just clicked would jump out from under the pointer.
  const compensate = useRef(0);
  // Whether the open panel's content has arrived, as its last measure said.
  const [panelReady, setPanelReady] = useState(false);
  // Whether the open panel has been brought into view for good: once its
  // content has arrived and been revealed, later growth is somebody using it.
  const revealed = useRef(false);

  const toggle = useCallback(
    (id: number) => {
      const index = shots.findIndex((shot) => shot.id === id);
      if (openId !== null && openId !== id && openIndex >= 0 && openIndex < index) {
        compensate.current = panelHeight;
      }
      setOpenId((current) => (current === id ? null : id));
      setPanelHeight(0);
      setPanelReady(false);
      revealed.current = false;
    },
    [shots, openId, openIndex, panelHeight],
  );

  const close = useCallback(() => {
    setOpenId(null);
    setPanelHeight(0);
    setPanelReady(false);
    revealed.current = false;
  }, []);

  // The panel only reports; the scrolling happens here. A child's layout
  // effect runs before its parent's, so a reveal run from the panel's own
  // measure came before this compensation on a swap to a lower row whose
  // detail was already cached — it measured against the old panel still
  // counted above, landed up to that panel's height short, and was marked done.
  const measure = useCallback((height: number, ready: boolean) => {
    setPanelHeight((current) => (Math.abs(current - height) < 0.5 ? current : height));
    setPanelReady(ready);
  }, []);

  // One effect, so the order is written down rather than implied: undo the
  // closed panel's height first, then bring the open panel into view.
  useLayoutEffect(() => {
    const container = scrollRef.current;
    if (container === null) return;
    if (compensate.current !== 0) {
      container.scrollTop = Math.max(0, container.scrollTop - compensate.current);
      compensate.current = 0;
    }
    if (openId === null || panelHeight === 0 || revealed.current) return;
    reveal(container, openId, `${panelPrefix}-${openId}`, headerRef.current);
    revealed.current = panelReady;
  }, [openId, panelHeight, panelReady, panelPrefix, scrollRef]);

  const visible = shots.slice(window.start, window.end);
  // One template for the header and every row, so a resized column cannot
  // leave its heading behind.
  const templates = gridTemplates(columns, widths);
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
        ref={headerRef}
        className={cn(
          "sticky top-0 z-10 flex items-center gap-2 border-border border-b bg-background",
          "py-1 pr-3 pl-2 text-muted-foreground text-xs uppercase tracking-wide",
        )}
      >
        <span className="size-3.5 shrink-0" aria-hidden="true" />
        <div className={cn(GRID, "min-w-0 flex-1")}>
          {columns.map((column) => (
            <HeaderCell
              key={column.id}
              column={column}
              sort={sort}
              order={order}
              onSort={onSort}
              width={widths[column.id]}
              onResize={onResize}
            />
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
            open={shot.id === openId}
            panelId={`${panelPrefix}-${shot.id}`}
            onToggle={toggle}
            onClose={close}
            onMeasure={measure}
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
  width,
  onResize,
}: {
  column: ShotColumn;
  sort: ShotSort;
  order: "asc" | "desc";
  onSort: (key: ShotSort) => void;
  width: number | undefined;
  onResize: (id: ShotColumnId, rem: number | null) => void;
}) {
  const key = SORTABLE[column.id];
  const active = key !== undefined && key === sort;
  const size = fixedSize(column);
  // Centred, titles and content alike: the columns are narrow and mostly a
  // badge, a star row or a short figure, and a centred heading over a centred
  // value reads as one column where a right-aligned number under a left-aligned
  // badge read as two. `relative` and no `overflow-hidden` here, because the
  // resize handle straddles the cell's right edge; the label truncates inside.
  const className = cn(column.narrowHidden && "hidden md:block", "relative min-w-0 text-center");
  const handle =
    size === null ? null : (
      <ResizeHandle
        column={column}
        size={size}
        width={columnWidth(size, width)}
        onResize={onResize}
      />
    );

  if (key === undefined) {
    return (
      <span className={className} data-testid={`header-${column.id}`}>
        <span className="block truncate">{column.label}</span>
        {handle}
      </span>
    );
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
          "inline-flex max-w-full items-center justify-center gap-1 uppercase tracking-wide hover:text-foreground",
          active && "text-foreground",
        )}
      >
        <span className="truncate">{column.label}</span>
        {active ? (
          order === "asc" ? (
            <ArrowUp className="size-3 shrink-0" aria-hidden="true" />
          ) : (
            <ArrowDown className="size-3 shrink-0" aria-hidden="true" />
          )
        ) : null}
        <span className="sr-only">
          {active
            ? `sorted ${order === "asc" ? "ascending" : "descending"}, click to reverse`
            : "click to sort by this column"}
        </span>
      </button>
      {handle}
    </span>
  );
}

/** One keyboard step, in rem; with Shift, four of them. */
const WIDTH_STEP = 0.25;

/** The root font size, which is what a rem is. jsdom reports none; 16 is every browser's default. */
function remInPixels(): number {
  const parsed = Number.parseFloat(getComputedStyle(document.documentElement).fontSize);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : 16;
}

/**
 * The drag handle on a heading's right edge.
 *
 * A sibling of the sort button rather than part of it, so a drag or a
 * double-click never sorts; the click is stopped as well, because a pointer
 * released over the handle still produces one. It is a focusable `separator`,
 * the ARIA pattern for a splitter, so a keyboard gets the same control a mouse
 * does: the arrows step it, Home and End go to the bounds, and it announces its
 * width.
 *
 * The drag uses pointer capture, so the width keeps following the pointer when
 * it leaves the few pixels of the handle — which it does at once, since the
 * handle is narrower than any real drag — and ends wherever the button is let
 * go, even outside the window.
 */
function ResizeHandle({
  column,
  size,
  width,
  onResize,
}: {
  column: ShotColumn;
  size: FixedSize;
  width: number;
  onResize: (id: ShotColumnId, rem: number | null) => void;
}) {
  const drag = useRef<{ pointerId: number; x: number; start: number; px: number } | null>(null);

  function set(rem: number) {
    onResize(column.id, clampWidth(size, rem));
  }

  return (
    // biome-ignore lint/a11y/useSemanticElements: an `<hr>` cannot take focus or a value; a focusable separator is the ARIA splitter. A span, positioned, because the heading it sits in is phrasing content.
    <span
      role="separator"
      aria-orientation="vertical"
      aria-label={`Resize the ${column.label} column`}
      aria-valuenow={width}
      aria-valuemin={size.min}
      aria-valuemax={size.max}
      aria-valuetext={`${width} rem`}
      tabIndex={0}
      data-testid={`resize-${column.id}`}
      title="Drag to resize, double-click to reset"
      className={cn(
        "-right-2 absolute top-0 z-[1] flex h-full w-3 cursor-col-resize touch-none select-none justify-center",
        "after:h-full after:w-px after:bg-border hover:after:bg-foreground/40",
        "focus-visible:outline-none focus-visible:after:w-0.5 focus-visible:after:bg-ring",
      )}
      onPointerDown={(event) => {
        if (event.button !== 0) return;
        // No text selection and no focus fight while dragging.
        event.preventDefault();
        event.currentTarget.setPointerCapture?.(event.pointerId);
        drag.current = {
          pointerId: event.pointerId,
          x: event.clientX,
          start: width,
          px: remInPixels(),
        };
      }}
      onPointerMove={(event) => {
        const current = drag.current;
        if (current === null || current.pointerId !== event.pointerId) return;
        set(current.start + (event.clientX - current.x) / current.px);
      }}
      onPointerUp={(event) => {
        drag.current = null;
        event.currentTarget.releasePointerCapture?.(event.pointerId);
      }}
      onPointerCancel={() => {
        drag.current = null;
      }}
      onClick={(event) => event.stopPropagation()}
      onDoubleClick={(event) => {
        event.stopPropagation();
        onResize(column.id, null);
      }}
      onKeyDown={(event) => {
        const step = event.shiftKey ? WIDTH_STEP * 4 : WIDTH_STEP;
        const next =
          event.key === "ArrowLeft"
            ? width - step
            : event.key === "ArrowRight"
              ? width + step
              : event.key === "Home"
                ? size.min
                : event.key === "End"
                  ? size.max
                  : null;
        if (next === null) return;
        event.preventDefault();
        set(next);
      }}
    />
  );
}

/**
 * Scroll the list just enough to show a freshly opened panel.
 *
 * The measuring half; the arithmetic is `revealDistance`.
 */
function reveal(
  container: HTMLElement,
  openId: number,
  panelId: string,
  header: HTMLElement | null,
): void {
  const panel = document.getElementById(panelId);
  const row = panel?.previousElementSibling;
  if (!panel || !row || Number(panel.dataset.shot) !== openId) return;
  const box = container.getBoundingClientRect();
  const by = revealDistance({
    listTop: box.top + (header?.offsetHeight ?? 0),
    listBottom: box.bottom,
    rowTop: row.getBoundingClientRect().top,
    panelBottom: panel.getBoundingClientRect().bottom,
  });
  if (by > 0) container.scrollTop += by;
}

/**
 * How far down to scroll so an open panel is on screen, in pixels.
 *
 * Only ever down, and never so far that the row itself goes under the sticky
 * header (`listTop` is the header's bottom edge): the row is what was clicked
 * and what closes the panel again, so it matters more than the bottom of a
 * panel taller than the list. Zero when the panel already fits, which is most
 * opens. Exported for its test; jsdom has no rectangles to measure.
 */
export function revealDistance({
  listTop,
  listBottom,
  rowTop,
  panelBottom,
}: {
  listTop: number;
  listBottom: number;
  rowTop: number;
  panelBottom: number;
}): number {
  const overflow = panelBottom - listBottom;
  if (overflow <= 0) return 0;
  return Math.min(overflow, Math.max(0, rowTop - listTop));
}

/**
 * A field that keeps what is typed into it: text inputs of every kind, text
 * areas, selects and anything contenteditable. A checkbox or a radio has
 * nothing to lose, so Escape on one still closes the panel.
 */
function isEditable(target: Element): boolean {
  if (target instanceof HTMLTextAreaElement || target instanceof HTMLSelectElement) return true;
  if (target instanceof HTMLInputElement) {
    return target.type !== "checkbox" && target.type !== "radio";
  }
  return target instanceof HTMLElement && target.isContentEditable;
}

/** Things inside a row that own Escape themselves: an open popover, and its trigger. */
const OWNS_ESCAPE = '[role="dialog"], [aria-haspopup][aria-expanded="true"]';

function ShotRow({
  shot,
  columns,
  selected,
  onToggleSelected,
  selectionFull,
  open,
  panelId,
  onToggle,
  onClose,
  onMeasure,
}: {
  shot: ShotListRow;
  columns: ShotColumn[];
  selected: boolean;
  onToggleSelected: (id: number) => void;
  selectionFull: boolean;
  open: boolean;
  panelId: string;
  onToggle: (id: number) => void;
  onClose: () => void;
  onMeasure: (height: number, ready: boolean) => void;
}) {
  const toggleRef = useRef<HTMLButtonElement>(null);

  // Escape anywhere in the row or its panel closes the panel and puts focus
  // back on the row, which is where a keyboard user opened it from — unless
  // the key belongs to something else. A popover inside the row (the
  // needs-a-Set menu, the row editor) closes itself and must not take the panel
  // with it. A text field owns Escape too: it is the key that dismisses an
  // autocomplete list, and an IME composition cancelled with it still delivers
  // the keydown — closing the panel then would unmount the judgement form and
  // throw away the verdict being typed. The row toggle is a Shift+Tab away.
  // The row is on screen whenever its panel has focus, so the focus lands on
  // something visible.
  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key !== "Escape" || !open) return;
    if (event.nativeEvent.isComposing) return;
    const target = event.target as Element;
    if (target.closest(OWNS_ESCAPE) || isEditable(target)) return;
    event.preventDefault();
    onClose();
    toggleRef.current?.focus({ preventScroll: true });
  }

  return (
    // biome-ignore lint/a11y/noStaticElementInteractions: Escape from anywhere inside the row and its panel, which are not one control.
    <div data-testid="shot-entry" onKeyDown={onKeyDown}>
      <div
        data-testid="shot-row"
        data-shot={shot.id}
        data-open={open ? "" : undefined}
        style={{ height: ROW_HEIGHT }}
        className={cn(
          "relative flex items-center gap-2 border-border border-b pr-3 pl-2 hover:bg-muted/40",
          open && "bg-muted/40",
        )}
      >
        {/* The whole row toggles its panel, but the button does not *wrap* the
            row: the rating cell holds five buttons and the row ends in
            another, and a button inside a button is invalid HTML. So the toggle
            is one stretched overlay and the controls sit above it — the same
            arrangement the row used when it was a link to the shot page. */}
        <button
          ref={toggleRef}
          type="button"
          data-testid="row-toggle"
          aria-expanded={open}
          aria-controls={open ? panelId : undefined}
          aria-label={`Shot ${shot.device_id}`}
          onClick={() => onToggle(shot.id)}
          className="absolute inset-0 cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset"
        />
        <input
          type="checkbox"
          className={cn(INTERACTIVE, "size-3.5 shrink-0 accent-primary")}
          checked={selected}
          // Three is the limit the compare drawer draws; a fourth line makes an
          // overlay unreadable rather than more informative.
          disabled={!selected && selectionFull}
          onChange={() => onToggleSelected(shot.id)}
          aria-label={`Compare shot ${shot.device_id}`}
        />
        <div className={cn(GRID, "min-w-0 flex-1 py-1")}>
          {/* Cells are divs, not spans: the Set cell can hold an anchored
              panel, which is block content. */}
          {columns.map((column) => (
            <div
              key={column.id}
              data-column={column.id}
              className={cn(
                "min-w-0",
                // Only the cells that can be clicked come above the stretched
                // toggle. The rest stay under it, which is what keeps the whole
                // row a toggle rather than only its gaps.
                column.id === "rating" && INTERACTIVE,
                // Every cell's content is inline-level (a badge, the stars, a
                // figure), so centring the text centres the content; the text
                // columns stay `block truncate` inside it.
                "text-center",
                column.narrowHidden && "hidden md:block",
              )}
            >
              <Cell shot={shot} id={column.id} />
            </div>
          ))}
        </div>
        <ShotRowEditor shot={shot} className={INTERACTIVE} />
      </div>
      {open ? <ShotRowPanel shot={shot} id={panelId} onMeasure={onMeasure} /> : null}
    </div>
  );
}

/**
 * Above the stretched toggle, so a click here is a click on this and not the row.
 *
 * `z-[1]`, not `z-10`: all it has to beat is the toggle's own `z-auto`, and the
 * sticky header is `z-10` in the same stacking context — at `z-10` these
 * painted *over* the header as the list scrolled under it.
 */
const INTERACTIVE = "relative z-[1]";

function Cell({ shot, id }: { shot: ShotListRow; id: ShotColumnId }) {
  switch (id) {
    case "time":
      return (
        <span
          className="block truncate whitespace-nowrap text-sm tabular-nums"
          // The compact form folds the year or the minute away; the whole
          // timestamp is one hover away rather than one page away.
          title={shot.started_at ? formatTime(shot.started_at) : undefined}
        >
          {formatListTime(shot.started_at)}
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
      // The badge itself is lifted above the row's stretched toggle, not the
      // cell: the empty rest of the cell still toggles the row, and no
      // ancestor of the menu gets a z-index — one would trap the menu's own
      // `z-50` inside this row, under the next row's controls and the sticky
      // header.
      return (
        <div data-testid="set-badge-slot">
          {shot.set_badge ? (
            <SetBadge badge={shot.set_badge} className={cn(INTERACTIVE, "max-w-full")} />
          ) : (
            <NeedsSetMenu shot={shot} className={INTERACTIVE} />
          )}
        </div>
      );
    case "notes":
      return (
        <span
          className="block truncate text-muted-foreground text-xs"
          data-testid="notes-cell"
          // Two hundred characters into a column a few wide: the whole note on
          // hover is the difference between a column worth turning on and a
          // column of first words.
          title={shot.judgement_notes ?? undefined}
        >
          {shot.judgement_notes || ""}
        </span>
      );
    case "analyze":
      // Lifted like the stars: a click on the button — or on the "Analysing…"
      // beside where it was — is never a click on the row.
      return <AnalyseCell shot={shot} className={INTERACTIVE} />;
    case "flags":
      return (
        <span className="flex flex-wrap justify-center gap-1">
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
 * (`usePatchJudgement`), because `PUT` replaces the row and the flavour notes,
 * doses, grind and decision typed elsewhere must survive a star.
 *
 * What is shown and what a click means are two different numbers, which is why
 * `ownRating` is passed separately. A row shows the machine's own notes-card
 * rating when this box holds no verdict; clicking that star has to record the
 * value, not clear a verdict that does not exist — and "clear" on a shot with
 * no judgement would write an empty one, which is a row that claims somebody
 * had an opinion, ages past the machine's notes card, and would be pushed back
 * over what was typed at the machine.
 */
function RatingCell({ shot }: { shot: ShotListRow }) {
  const patch = usePatchJudgement(shot.id);
  const own = shot.judgement_rating ?? null;
  return (
    <RatingStars
      rating={ratingOf(shot)}
      ownRating={own}
      label={`shot ${shot.device_id}`}
      onRate={(rating) => {
        // Clearing a rating nobody set is not a change, and it is the one
        // click here that could create a verdict out of nothing.
        if (rating === null && own === null) return;
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
