import { Badge } from "@/components/ui/badge";
import { formatScore, scoreBand, type Tone } from "@/lib/shots";
import { cn } from "@/lib/utils";

const TONE_CLASS: Record<Tone, string> = {
  good: "border-status-good/40 bg-status-good/10 text-status-good-text",
  warn: "border-status-warn/40 bg-status-warn/10 text-status-warn-text",
  bad: "border-status-bad/40 bg-status-bad/10 text-status-bad-text",
  neutral: "border-border bg-muted text-muted-foreground",
};

/**
 * The execution score, banded by colour.
 *
 * Deliberately not a rating: this is how cleanly the *machine* executed the
 * shot, and a ten here says nothing about whether the coffee was good
 * (`gaggiclanker/domain/scoring.py`). The two are kept visually distinct — a
 * number in a coloured pill against a row of stars — so they are never read as
 * one judgement.
 */
export function ScoreBadge({ score, className }: { score: number | null; className?: string }) {
  const band = scoreBand(score);
  return (
    <Badge
      variant="outline"
      data-testid="score-badge"
      data-tone={band.tone}
      title={`Execution ${formatScore(score)} — ${band.label}`}
      className={cn("tabular-nums", TONE_CLASS[band.tone], className)}
    >
      {formatScore(score)}
    </Badge>
  );
}
