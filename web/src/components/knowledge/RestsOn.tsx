import { ArrowRight } from "lucide-react";
import type { InsightRestsOn } from "@/api/types";
import { OutcomeBadge } from "@/components/sets/OutcomeBadge";

/**
 * The versions a Set insight rests on, each with its outcome as it stands now.
 *
 * An insight written when v3 "held" can outlive the day v3 was re-graded, so the
 * outcome shown is the version's **current** one, and when it moved since the
 * insight was written the old one is shown before it ("held → failed") with a
 * mark that is visible without opening anything. A cleared outcome reads "no
 * outcome now". Renders nothing for an insight that rests on shots alone.
 */
export function RestsOn({ rests }: { rests: InsightRestsOn[] }) {
  if (rests.length === 0) return null;
  return (
    <div
      className="flex flex-wrap items-center gap-x-3 gap-y-1 text-muted-foreground text-xs"
      data-testid="rests-on"
    >
      <span>rests on</span>
      {rests.map((item) => (
        <span
          key={item.set_version_id}
          className="inline-flex items-center gap-1"
          data-testid="rests-on-version"
          data-version={item.label}
          data-changed={item.changed}
        >
          <span className="font-mono">{item.label}</span>
          {item.changed ? (
            <>
              <OutcomeBadge state={item.outcome_then ?? "open"} />
              <ArrowRight className="size-3" aria-hidden="true" />
              {item.outcome_now ? (
                <OutcomeBadge state={item.outcome_now} />
              ) : (
                <span className="font-medium text-foreground">no outcome now</span>
              )}
              <span
                className="rounded-full border border-status-warn/40 bg-status-warn/10 px-1.5 text-status-warn-text"
                data-testid="rests-on-changed"
              >
                changed since
              </span>
            </>
          ) : (
            <OutcomeBadge state={item.outcome_now ?? "open"} />
          )}
        </span>
      ))}
    </div>
  );
}
