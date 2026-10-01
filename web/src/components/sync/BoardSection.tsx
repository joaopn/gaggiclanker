import { AlertTriangle } from "lucide-react";
import { useState } from "react";
import { SectionCard } from "@/components/layout/SectionCard";
import { ConfirmStrip } from "@/components/sync/ConfirmStrip";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useSyncStatus } from "@/hooks/useArchive";
import { useProfileBoard, useResumeBoard } from "@/hooks/useBoard";
import { type BoardSummaryItem, boardSummaryOf, summaryLine } from "@/lib/board";
import { formatTime } from "@/lib/shots";

type Section =
  | "adopted"
  | "pushed"
  | "overwritten"
  | "removed"
  | "left"
  | "homeScreen"
  | "failures";

const SECTIONS: { key: Section; title: string }[] = [
  { key: "adopted", title: "Taken onto the board from the machine" },
  { key: "pushed", title: "Put on the machine" },
  { key: "overwritten", title: "Put beside a copy that was edited on the display" },
  { key: "removed", title: "Removed from the machine" },
  { key: "left", title: "Left on the machine" },
  { key: "homeScreen", title: "Home screen" },
  { key: "failures", title: "Did not work" },
];

/**
 * What the last pull did to the machine's profiles, and the way back from a pause.
 *
 * With the Writes switch on, a pull makes the machine's profiles match the board; this
 * says what that came to (put on, removed, left and why, stars moved, failures) from the
 * run's own summary, and what the board still cannot fix by itself (a profile of yours
 * that was edited or is missing). When the machine looks reset, with none of the app's
 * profiles on it, the pull stops writing rather than push the whole board back; the
 * banner says so and Resume (after a confirmation) lets the next pull do it.
 */
export function BoardSection() {
  const sync = useSyncStatus();
  const board = useProfileBoard();
  const resume = useResumeBoard();
  const [confirming, setConfirming] = useState(false);

  const run = sync.data?.last_runs?.profiles;
  const summary = boardSummaryOf(run);
  const view = board.data;
  const paused = view?.paused ?? summary?.paused ?? null;
  const reports = view?.reports ?? [];
  const writesOn = view?.writes_enabled ?? false;

  return (
    <SectionCard
      title="Profile board"
      description={
        run?.finished_at
          ? `What the last pull did to the machine's profiles (${formatTime(run.finished_at)}).`
          : "What a pull does to the machine's profiles."
      }
      actions={
        writesOn ? (
          <Badge variant="secondary">writes on</Badge>
        ) : (
          <Badge variant="outline">writes off</Badge>
        )
      }
    >
      {sync.isPending || board.isPending ? (
        <Skeleton className="h-16 w-full" />
      ) : (
        <div className="space-y-3">
          {paused ? (
            <div
              className="space-y-2 rounded-md border border-status-warn/40 bg-status-warn/10 p-3"
              data-testid="board-paused-banner"
            >
              <p className="flex items-center gap-1.5 font-medium text-sm text-status-warn-text">
                <AlertTriangle className="size-3.5" aria-hidden="true" />
                Pulls are not writing profiles to the machine
              </p>
              <p className="text-status-warn-text text-xs">
                The machine looks reset: none of the profiles the app put on it is there any more,
                so a pull writes nothing until you say it may. This stops a pull from refilling a
                machine somebody has just wiped without being asked.
              </p>
              {confirming ? (
                <ConfirmStrip
                  title="Let pulls write again?"
                  confirmLabel="Resume"
                  confirmVariant="default"
                  testId="board-resume-confirm"
                  onCancel={() => setConfirming(false)}
                  onConfirm={() => {
                    setConfirming(false);
                    resume.mutate();
                  }}
                >
                  The next pull will push the app's profiles back onto the machine. Profiles of
                  yours are never pushed.
                </ConfirmStrip>
              ) : (
                <Button
                  size="sm"
                  variant="outline"
                  disabled={resume.isPending}
                  data-testid="board-resume"
                  onClick={() => setConfirming(true)}
                >
                  Resume
                </Button>
              )}
            </div>
          ) : null}

          {summary === null ? (
            <p className="text-muted-foreground text-sm" data-testid="board-summary-none">
              {writesOn
                ? "The last pull made no change to the machine's profiles, or has not run since writes were turned on."
                : "Writes are off, so a pull does not change the machine's profiles."}
            </p>
          ) : (
            <BoardSummary summary={summary} />
          )}

          {reports.length > 0 ? (
            <div data-testid="board-reports">
              <h3 className="mb-1 font-medium text-sm">Needs a look</h3>
              <ul className="list-disc space-y-0.5 pl-5 text-muted-foreground text-xs">
                {reports.map((report) => (
                  <li
                    key={`${report.row_id}-${report.reason}-${report.device_id ?? ""}`}
                    className="break-words"
                  >
                    <span className="text-foreground">{report.label}</span>
                    {report.detail ? `: ${report.detail}` : ""}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </div>
      )}
    </SectionCard>
  );
}

function BoardSummary({ summary }: { summary: NonNullable<ReturnType<typeof boardSummaryOf>> }) {
  const shown = SECTIONS.map(({ key, title }) => ({
    key,
    title,
    items: summary[key] as BoardSummaryItem[],
  })).filter((section) => section.items.length > 0);

  if (shown.length === 0) {
    return (
      <p className="text-muted-foreground text-sm" data-testid="board-summary-nothing">
        {summary.paused
          ? "The last pull wrote nothing: the machine looked reset."
          : "The last pull found the machine already matching the board."}
      </p>
    );
  }
  return (
    <div className="space-y-3" data-testid="board-summary">
      <div className="flex flex-wrap gap-1.5" data-testid="board-summary-counts">
        {shown.map((section) => (
          <Badge
            key={section.key}
            variant={section.key === "failures" ? "destructive" : "secondary"}
          >
            {section.items.length} {section.title.toLowerCase()}
          </Badge>
        ))}
      </div>
      {shown.map((section) => (
        <div key={section.key} data-testid={`board-summary-${section.key}`}>
          <h3 className="mb-1 font-medium text-sm">{section.title}</h3>
          <ul className="list-disc space-y-0.5 pl-5 text-muted-foreground text-xs">
            {section.items.map((item, index) => (
              <li
                // Two lines can share a label and a reason; the order is stable.
                // biome-ignore lint/suspicious/noArrayIndexKey: a summary is read-only
                key={`${item.label}-${index}`}
                className="break-words"
              >
                {summaryLine(item, section.key)}
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}
