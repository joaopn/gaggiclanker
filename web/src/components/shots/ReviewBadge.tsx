import { Loader2 } from "lucide-react";
import { useId } from "react";
import type { ReadingBlock, ShotWarning } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

/**
 * What the shots table's Review column, the Set history and the compare tray say
 * about a shot, in one place.
 *
 * One component for every state the badge has, so a state is added here and not
 * beside it. The text is the server's (`badge`, built by code from the merged
 * entries or from the reading's state, never from a model) and what this adds is
 * the look:
 *
 * - **Entries** (a failed critical or important expectation, then the warnings,
 *   free-text results the reading failed among them): "ramp: early yield +4". The
 *   tone is the **first entry's** severity, so a reader sees what the chat is told:
 *   red is a critical expectation failed, amber an important one failed or a warning
 *   nothing marks as expected, grey a warning the signature expects.
 * - **Unread, nothing to say**: a neutral outlined `Review`, the thing to press. Where nothing
 *   can be pressed (the Set history, the compare tray) it draws nothing.
 * - **Running**: `Reading…` with a spinner. **Failed**: a grey `Failed to run`, the
 *   reason in the tooltip.
 * - **Read, no entries**: a green `As intended` (a confirmed signature held), or a
 *   grey `No signature`.
 *
 * **Outlined while claims wait for an answer, filled once none does.** The filled look is
 * the tint the badge always had; the outlined one has a transparent background. A
 * `Review` nobody has read is outlined too: nothing has been confirmed of it.
 *
 * Only the shots table gives it an `onPress`, and then only while the shot can be read:
 * the badge is a real `<button>` (unread and failed shots start a reading, a read one is
 * taken to the Reading card), inert while a reading runs (`aria-disabled`, and the press is
 * ignored whatever started it: click, Enter or Space). Everywhere else it is the plain
 * label. The press stops propagation, so the row behind it never opens.
 *
 * The full list is the hover (`title` on the wrapper, which is not the described
 * element; unverified entries marked, the reading's summary last) and, for a screen
 * reader, one sentence the badge `aria-describedby`s, so it is read once. Not a popover:
 * a panel that opens on hover would fight the click. The wrapper keeps the structure
 * valid: a button and its description are siblings, never nested in a span. As a button
 * the description is `hidden`, not `sr-only`: `aria-describedby` still reads hidden
 * content, whereas an `sr-only` sentence is also in the reading order and would be
 * announced on the button and again when reading on.
 *
 * The count ("+2") sits outside the span that truncates, so a long phase name is
 * cut and the count is not.
 */

export type Tone = "warn" | "bad" | "muted" | "good";

/** Filled is the tint the badge always had; outlined keeps the border and drops the fill. */
export const TONE_CLASS: Record<Tone, { filled: string; outlined: string }> = {
  warn: {
    filled: "border-status-warn/40 bg-status-warn/10 text-status-warn-text",
    outlined: "border-status-warn/60 bg-transparent text-status-warn-text",
  },
  bad: {
    filled: "border-status-bad/40 bg-status-bad/10 text-status-bad-text",
    outlined: "border-status-bad/60 bg-transparent text-status-bad-text",
  },
  good: {
    filled: "border-status-good/40 bg-status-good/10 text-status-good-text",
    outlined: "border-status-good/60 bg-transparent text-status-good-text",
  },
  muted: {
    filled: "border-border bg-muted/50 text-muted-foreground",
    outlined: "border-border bg-transparent text-muted-foreground",
  },
};

/** The badge's colour: the first entry's severity, and amber for one the server did not name. */
export function toneOf(severity: string | undefined): Tone {
  if (severity === "red") return "bad";
  if (severity === "grey") return "muted";
  return "warn";
}

/** One warning as a line: "ramp: fast flow — the sentence with the numbers". */
export function warningLine(warning: ShotWarning): string {
  const line = `${warning.phase}: ${warning.fault} — ${warning.detail}`;
  // A free-text result nobody has confirmed yet: said in the tooltip, as it is on the card. The
  // mark goes before the sentence's full stop ("…fall together (unverified)."), and `sentences`
  // adds the stop back for the screen-reader text.
  return warning.unverified ? `${line.replace(/[.!?]$/, "")} (unverified)` : line;
}

/** The lines as one text: each ends in a full stop, and one that already does gets no second. */
function sentences(lines: string[]): string {
  return lines.map((line) => (/[.!?]$/.test(line) ? line : `${line}.`)).join(" ");
}

/** What the badge says when the server sent no text for a state that always has one. */
const STATE_TEXT = { unread: "Review", running: "Reading…", failed: "Failed to run" } as const;

export type BadgeView = {
  text: string;
  tone: Tone;
  filled: boolean;
  /** The tooltip's lines: the reason, the entries, the summary. */
  lines: string[];
  /** Whether the text is the first entry's "phase: fault", which truncates by its own rule. */
  entries: boolean;
  spinner: boolean;
};

/** What a shot's served badge, entries and reading block come to on screen; `null` for nothing. */
export function badgeView(
  badge: string | null | undefined,
  warnings: ShotWarning[] | undefined,
  reading: ReadingBlock | undefined,
): BadgeView | null {
  const entries = warnings ?? [];
  const entryLines = entries.map(warningLine);
  const state = reading?.state;
  const tail = reading?.summary ? [reading.summary] : [];

  if (state === "running") {
    return {
      text: badge || STATE_TEXT.running,
      tone: "muted",
      filled: false,
      lines: [...entryLines, ...tail],
      entries: false,
      spinner: true,
    };
  }
  if (state === "failed") {
    return {
      text: badge || STATE_TEXT.failed,
      tone: "muted",
      filled: true,
      lines: [
        reading?.reason ? `Failed to run: ${reading.reason}` : "Failed to run",
        ...entryLines,
        ...tail,
      ],
      entries: false,
      spinner: false,
    };
  }
  const unanswered = (reading?.unanswered ?? 0) > 0;
  if (entries.length > 0 && badge) {
    return {
      text: badge,
      tone: toneOf(entries[0]?.severity),
      // An unread or unreadable shot has nothing to answer: the tint it always had.
      filled: state === "read" ? !unanswered : true,
      lines: [...entryLines, ...tail],
      entries: true,
      spinner: false,
    };
  }
  if (state === "read") {
    const asIntended = reading?.verdict === "as_intended";
    // "No signature" is said only of a reading whose verdict is that. A verdict of failures with
    // the entries not (yet) known, as when the fields failed to load, is a neutral wait, never a
    // word that would contradict the failures once they arrive.
    const fallback =
      reading?.verdict === "as_intended"
        ? "As intended"
        : reading?.verdict === "no_signature"
          ? "No signature"
          : "Failures loading…";
    return {
      text: badge || fallback,
      tone: asIntended ? "good" : "muted",
      filled: !unanswered,
      lines: tail,
      entries: false,
      spinner: false,
    };
  }
  if (state === "unread") {
    return {
      text: badge || STATE_TEXT.unread,
      tone: "muted",
      filled: false,
      lines: [],
      entries: false,
      spinner: false,
    };
  }
  // Not readable, or a caller with no reading block: only what is plainly wrong, or nothing.
  return null;
}

export function ReviewBadge({
  badge,
  warnings,
  reading,
  onPress,
  className,
}: {
  /** The server's text for the list, or `null` when there is nothing to say. */
  badge: string | null | undefined;
  /** Most severe first, as the server orders them. */
  warnings: ShotWarning[] | undefined;
  /** The reading block the row carries; without it the badge is the checks' entries alone. */
  reading?: ReadingBlock;
  /** Given only by the shots table: makes the badge a button while the shot can be read. */
  onPress?: () => void;
  className?: string;
}) {
  const listId = useId();
  const view = badgeView(badge, warnings, reading) ?? entriesOnly(badge, warnings);
  // `Review` is the thing to press: where nothing can be pressed (the Set history, the compare
  // tray) a shot nobody has read draws nothing, like a shot with nothing to say.
  if (!view || (reading?.state === "unread" && !view.entries && onPress === undefined)) return null;
  const state = reading?.state;
  const readable = state !== undefined && state !== "not_readable";
  const asButton = onPress !== undefined && readable;
  const running = state === "running";
  const hint =
    asButton && !running
      ? state === "read"
        ? "Open the reading"
        : "Press to have a model read this shot"
      : null;
  const lines = view.lines;
  const first = warnings?.[0];
  // "+N" is the server's own text for the other warnings; it is split off only
  // so that it is not the part that truncates.
  const more = view.entries && warnings && warnings.length > 1 ? ` +${warnings.length - 1}` : "";
  const lead = more && view.text.endsWith(more) ? view.text.slice(0, -more.length) : view.text;
  // The same split once more: the phase is the only part that may be cut. A long phase name
  // would otherwise take the fault word with it ("Final push to t… +1"), and the fault is what
  // the badge is for. The server's text is "<phase>: <fault>", so the prefix is known.
  const phase = first?.phase ?? "";
  const split = view.entries && phase !== "" && lead.startsWith(`${phase}: `);
  const described = [...lines, ...(hint ? [hint] : [])];

  const body = (
    <>
      {view.spinner ? (
        <Loader2
          className="mr-1 size-3 shrink-0 animate-spin"
          aria-hidden="true"
          data-testid="review-badge-spinner"
        />
      ) : null}
      {split ? (
        // One flexible box holds the phase and the fault, and the count stays outside it.
        // The phase is the only part that shrinks (`truncate`); the fault keeps its own width
        // and is clipped by the box only once the phase is gone, with an ellipsis of its own.
        // Shrink factors are not used: flex shares a shortfall out by factor times width, so
        // a fault that is meant to stay whole loses a fraction of a pixel while the phase is cut.
        <span className="flex min-w-0 flex-1 overflow-hidden" data-testid="review-badge-text">
          <span className="min-w-0 truncate" data-testid="review-badge-phase">
            {phase}
          </span>
          <span
            className="max-w-full shrink-0 truncate whitespace-pre"
            data-testid="review-badge-fault"
          >
            {lead.slice(phase.length)}
          </span>
        </span>
      ) : (
        <span className="min-w-0 truncate">{lead}</span>
      )}
      {more ? <span className="shrink-0 whitespace-pre">{more}</span> : null}
    </>
  );

  const common = {
    "data-testid": "review-badge",
    "data-tone": view.tone,
    "data-filled": view.filled ? "yes" : "no",
    "data-state": state ?? "entries",
    "aria-describedby": listId,
  } as const;
  const look = cn(
    "min-w-0 max-w-full shrink gap-0",
    TONE_CLASS[view.tone][view.filled ? "filled" : "outlined"],
  );

  return (
    <span
      className={cn("inline-flex min-w-0 max-w-full overflow-hidden", className)}
      title={lines.join("\n") || undefined}
      data-testid="review-badge-wrap"
    >
      {asButton ? (
        <Badge variant="outline" asChild className={cn(look, "cursor-pointer")}>
          <button
            type="button"
            {...common}
            aria-disabled={running ? true : undefined}
            className={cn(running && "cursor-progress")}
            onClick={(event) => {
              // The row behind is a toggle: a press on the badge is never the row's.
              event.stopPropagation();
              if (running) return;
              onPress();
            }}
          >
            {body}
          </button>
        </Badge>
      ) : (
        <Badge variant="outline" {...common} className={look}>
          {body}
        </Badge>
      )}
      <span id={listId} className={asButton ? "hidden" : "sr-only"} data-testid="review-badge-list">
        {sentences(described)}
      </span>
    </span>
  );
}

/** A caller with no reading block, or a shot that cannot be read: the entries alone, or nothing. */
function entriesOnly(
  badge: string | null | undefined,
  warnings: ShotWarning[] | undefined,
): BadgeView | null {
  if (!badge || !warnings || warnings.length === 0) return null;
  return {
    text: badge,
    tone: toneOf(warnings[0]?.severity),
    filled: true,
    lines: warnings.map(warningLine),
    entries: true,
    spinner: false,
  };
}
