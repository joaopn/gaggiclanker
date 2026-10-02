import { AlertTriangle } from "lucide-react";
import { Link } from "react-router-dom";
import { SectionCard } from "@/components/layout/SectionCard";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { useSyncStatus } from "@/hooks/useArchive";
import { useProfileBoard } from "@/hooks/useBoard";
import { type BoardSummaryItem, boardSummaryOf, summaryLine } from "@/lib/board";
import { formatTime } from "@/lib/shots";

type Section =
  | "adopted"
  | "conflicts"
  | "recorded"
  | "pushed"
  | "removed"
  | "left"
  | "homeScreen"
  | "failures";

const SECTIONS: { key: Section; title: string }[] = [
  { key: "adopted", title: "Added to the list from the machine" },
  { key: "conflicts", title: "In conflict, left alone" },
  { key: "recorded", title: "Edits on the machine kept as versions" },
  { key: "pushed", title: "Put on the machine" },
  { key: "removed", title: "Removed from the machine" },
  { key: "left", title: "Left on the machine" },
  { key: "homeScreen", title: "Home screen" },
  { key: "failures", title: "Did not work" },
];

/**
 * What the last sync did to the machine's profiles.
 *
 * With the Writes switch on, a sync makes the machine hold exactly the profiles that are on in
 * the Profiles list; this says what that came to (put on, removed, left and why, stars moved,
 * conflicts left alone, failures) from the run's own summary, and what the list still cannot fix
 * by itself. When the machine looks reset the sync stops writing; the Profiles page asks what
 * to do, and this only points there.
 */
export function BoardSection() {
  const sync = useSyncStatus();
  const board = useProfileBoard();
  const run = sync.data?.last_runs?.profiles;
  const summary = boardSummaryOf(run);
  const view = board.data;
  // The board's own answer is the truth about a pause: once it has answered, the last run's
  // summary (which still says "paused" after a Resume) is history, not state. Only a board
  // that could not be read falls back to it.
  const paused = view ? (view.paused ?? null) : (summary?.paused ?? null);
  const reports = view?.reports ?? [];
  const writesOn = view?.writes_enabled ?? false;

  return (
    <SectionCard
      title="Profiles on the machine"
      description={
        run?.finished_at
          ? `What the last sync did to the machine's profiles (${formatTime(run.finished_at)}).`
          : "What a sync does to the machine's profiles."
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
        <div className="space-y-3" data-testid="board-section-body">
          {paused ? (
            <div
              className="space-y-1 rounded-md border border-status-warn/40 bg-status-warn/10 p-3"
              data-testid="board-paused-banner"
            >
              <p className="flex items-center gap-1.5 font-medium text-sm text-status-warn-text">
                <AlertTriangle className="size-3.5" aria-hidden="true" />
                Syncs are not writing profiles to the machine
              </p>
              <p className="text-status-warn-text text-xs">
                The machine looks reset, so a sync writes nothing until you say it may.{" "}
                <Link className="underline underline-offset-2" to="/profiles">
                  Decide on the Profiles page
                </Link>
                .
              </p>
            </div>
          ) : null}

          {summary === null ? (
            <p className="text-muted-foreground text-sm" data-testid="board-summary-none">
              {writesOn
                ? "The last sync made no change to the machine's profiles, or has not run since writes were turned on."
                : "Writes are off, so a sync does not change the machine's profiles."}
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
          ? "The last sync wrote nothing: the machine looked reset."
          : "The last sync found the machine already matching the profile list."}
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
