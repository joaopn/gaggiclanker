import { AlertTriangle } from "lucide-react";
import type { ShotWarning } from "@/api/types";
import { SectionCard } from "@/components/layout/SectionCard";
import { cn } from "@/lib/utils";

/**
 * What is plainly wrong with the shot, first on the page.
 *
 * One line per warning, most severe first as the server orders them: the badge's
 * own "phase: fault" and the sentence with the numbers in it. A shot with none
 * has **no card at all**. There is no "all clear": a warning needs no knowledge
 * of what the profile is for, so its absence is not a verdict on the shot, and a
 * green tick would read as one.
 */
export function ShotWarningsCard({ warnings }: { warnings: ShotWarning[] | undefined }) {
  if (!warnings || warnings.length === 0) return null;
  return (
    <SectionCard
      title="Warnings"
      description="Facts that need no knowledge of what the profile is for. Weigh each against that: a turbo profile runs fast on purpose."
    >
      <ul className="space-y-2" data-testid="shot-warnings">
        {warnings.map((warning) => (
          <li
            key={`${warning.phase}:${warning.fault}`}
            data-testid="warning-line"
            data-severity={warning.severity}
            className={cn(
              "flex items-start gap-2 rounded-md border px-3 py-2 text-sm",
              warning.severity === "red"
                ? "border-status-bad/40 bg-status-bad/10"
                : "border-status-warn/40 bg-status-warn/10",
            )}
          >
            <AlertTriangle
              className={cn(
                "mt-0.5 size-4 shrink-0",
                warning.severity === "red" ? "text-status-bad-text" : "text-status-warn-text",
              )}
              aria-hidden="true"
            />
            <div className="min-w-0">
              <p className="font-medium">
                {warning.phase}: {warning.fault}
              </p>
              <p className="text-muted-foreground">{warning.detail}</p>
            </div>
          </li>
        ))}
      </ul>
    </SectionCard>
  );
}
