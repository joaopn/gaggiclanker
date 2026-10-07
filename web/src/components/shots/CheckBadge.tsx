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
 * missing warning is not a verdict.
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
