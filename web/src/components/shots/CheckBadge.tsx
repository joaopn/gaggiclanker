import { useId } from "react";
import type { ChecksBlock } from "@/api/types";
import {
  EntriesText,
  entryLine,
  sentences,
  TONE_CLASS,
  toneOf,
} from "@/components/shots/badgeParts";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

/**
 * What the Curve check column, the Set history and the compare tray say about a shot: only the
 * deterministic checks and warnings, never anything a model wrote.
 *
 * The text is the server's (`checks.badge`, built by code from the entries): the first entry,
 * "ramp: early yield", and "+N" for the others. The tone is the **first entry's** severity: red is
 * a critical expectation failed, amber an important one failed or a warning nothing marks as
 * expected, grey a warning the signature expects. A shot with nothing to say draws nothing: a
 * missing warning is not a verdict. (The shots table's Curve check cell, `CheckCell`, is the one
 * place that also says "Pass" and "Unchecked"; the Set history and the compare tray keep this
 * badge alone.)
 *
 * It is never a control. The full list is the hover (`title` on the wrapper, which is not the
 * described element) and, for a screen reader, one sentence the badge `aria-describedby`s, so it
 * is read once. Not a popover: nothing opens from a Curve check.
 */
export function CheckBadge({
  checks,
  className,
}: {
  checks: ChecksBlock | undefined;
  className?: string;
}) {
  const listId = useId();
  const entries = checks?.entries ?? [];
  if (!checks?.badge || entries.length === 0) return null;
  const lines = entries.map(entryLine);
  return (
    <span
      className={cn("inline-flex min-w-0 max-w-full overflow-hidden", className)}
      title={lines.join("\n")}
      data-testid="check-badge-wrap"
    >
      <Badge
        variant="outline"
        data-testid="check-badge"
        data-tone={toneOf(entries[0]?.severity)}
        aria-describedby={listId}
        className={cn(
          "min-w-0 max-w-full shrink gap-0",
          TONE_CLASS[toneOf(entries[0]?.severity)].filled,
        )}
      >
        <EntriesText text={checks.badge} entries={entries} testIdPrefix="check-badge" />
      </Badge>
      <span id={listId} className="sr-only" data-testid="check-badge-list">
        {sentences(lines)}
      </span>
    </span>
  );
}

/**
 * The shots table's Curve check cell: the entry badge when the checks found something, a green
 * "Pass" when the shot was read against a signature in force, at least one expectation was
 * measured and none is in the badge, and a grey "Unchecked" when nothing was held to anything. The
 * state is the server's (`checks.state`), never worked out here. Only the entry badge has a hover
 * list, so only it takes `className` (the lift above the row's toggle); a click on the other two
 * toggles the row.
 */
export function CheckCell({
  checks,
  className,
}: {
  checks: ChecksBlock | undefined;
  className?: string;
}) {
  if (!checks) return null;
  if (checks.state === "entries") return <CheckBadge checks={checks} className={className} />;
  const pass = checks.state === "pass";
  return (
    <Badge
      variant="outline"
      data-testid={pass ? "check-pass" : "check-unchecked"}
      data-tone={pass ? "good" : "muted"}
      className={TONE_CLASS[pass ? "good" : "muted"].filled}
    >
      {pass ? "Pass" : "Unchecked"}
    </Badge>
  );
}
