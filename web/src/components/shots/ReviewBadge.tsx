import { useId } from "react";
import type { ShotWarning } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

/**
 * What the shots table's Review column, the Set history and the compare tray say
 * about a shot, in one place.
 *
 * One component for every state the badge will have, so a state is added here
 * and not beside it. Today it has one: the warnings. Their text is the server's
 * (`badge`, "ramp: fast flow +2", built by code from the structured list) and
 * the tone is the most severe one's, so a reader sees what the chat is told. A
 * shot with no warnings has no badge at all: a missing warning is not a verdict,
 * and a green "all clear" would be one.
 *
 * Colour is the severity: amber is a warning that needs no knowledge of the
 * profile, red is a critical expectation failed (a signature's, later). The
 * reading's own states (unread, reading, as intended, no signature, failed to
 * run) and outlined-while-unverified versus filled-once-confirmed join as more
 * cases of the same `tone` and `filled` below.
 *
 * The full list is the hover (`title` on the wrapper, which is not the described
 * element) and, for a screen reader, one visually hidden sentence the badge
 * `aria-describedby`s, so it is read once. Not a popover: the badge is about to
 * become a button that starts a reading, and a panel that opens on hover would
 * fight its click. The wrapper is what keeps that structure valid then: a button
 * and its description are siblings, never nested in a span.
 *
 * **Rule for step 4:** the description is read once only because the badge is not
 * interactive today. When the badge becomes a `<button>`, the sibling description
 * must change from `sr-only` to `hidden`: `aria-describedby` still reads hidden
 * content, whereas an `sr-only` sentence is also in the reading order, so a screen
 * reader would announce it on the button and again when reading on.
 *
 * The count ("+2") sits outside the span that truncates, so a long phase name is
 * cut and the count is not.
 */

type Tone = "warn" | "bad";

const TONE_CLASS: Record<Tone, string> = {
  warn: "border-status-warn/40 bg-status-warn/10 text-status-warn-text",
  bad: "border-status-bad/40 bg-status-bad/10 text-status-bad-text",
};

/** One warning as a line: "ramp: fast flow — the sentence with the numbers". */
export function warningLine(warning: ShotWarning): string {
  return `${warning.phase}: ${warning.fault} — ${warning.detail}`;
}

/** The lines as one text: each ends in a full stop, and one that already does gets no second. */
function sentences(lines: string[]): string {
  return lines.map((line) => (/[.!?]$/.test(line) ? line : `${line}.`)).join(" ");
}

export function ReviewBadge({
  badge,
  warnings,
  className,
}: {
  /** The server's text for the list, or `null` when there is nothing to say. */
  badge: string | null | undefined;
  /** Most severe first, as the server orders them. */
  warnings: ShotWarning[] | undefined;
  className?: string;
}) {
  const listId = useId();
  if (!badge || !warnings || warnings.length === 0) return null;
  const tone: Tone = warnings[0]?.severity === "red" ? "bad" : "warn";
  const lines = warnings.map(warningLine);
  // "+N" is the server's own text for the other warnings; it is split off only
  // so that it is not the part that truncates.
  const more = warnings.length > 1 ? ` +${warnings.length - 1}` : "";
  const lead = more && badge.endsWith(more) ? badge.slice(0, -more.length) : badge;
  return (
    <span
      className={cn("inline-flex min-w-0 max-w-full", className)}
      title={lines.join("\n")}
      data-testid="review-badge-wrap"
    >
      <Badge
        variant="outline"
        data-testid="review-badge"
        data-tone={tone}
        aria-describedby={listId}
        className={cn("min-w-0 max-w-full gap-0", TONE_CLASS[tone])}
      >
        <span className="min-w-0 truncate">{lead}</span>
        {more ? <span className="shrink-0 whitespace-pre">{more}</span> : null}
      </Badge>
      <span id={listId} className="sr-only" data-testid="review-badge-list">
        {sentences(lines)}
      </span>
    </span>
  );
}
