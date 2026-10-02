import { AlertTriangle, ChevronRight } from "lucide-react";
import { useState } from "react";
import type { BoardView } from "@/api/types";
import { Button } from "@/components/ui/button";
import { useProfileBoard, useResumeBoard } from "@/hooks/useBoard";
import { useSingleFlight } from "@/hooks/useSingleFlight";
import { previewLine } from "@/lib/board";
import { cn } from "@/lib/utils";

function plural(count: number, noun: string): string {
  return `${count} ${noun}${count === 1 ? "" : "s"}`;
}

/**
 * The reset guard: when the machine holds none of the files the last sync left there (a
 * firmware update that wiped it, or the wrong address), a sync writes nothing. One question
 * and one button; the lines it would act on are one click away. Resuming sends nothing itself:
 * the next sync does what the lines say.
 *
 * What resuming would do is read from the machine now (the page's own read is the archive's
 * mirror, which after a wipe still lists the files that are gone, so it would say "put back 0").
 * Only while paused, once per opening of the page, and with its own query key so a board write
 * does not read the machine again. A machine that could not be read is said plainly: no numbers.
 */
export function ResetBanner({ view }: { view: BoardView }) {
  const resume = useResumeBoard();
  const once = useSingleFlight();
  const [showLines, setShowLines] = useState(false);
  const live = useProfileBoard({ live: true, enabled: Boolean(view.paused) });
  if (!view.paused) return null;
  const reading = live.isPending;
  // Only a read made from the machine says what resuming would do. When the machine could not
  // be read the page does not fall back to the stored copy's numbers: that copy still lists
  // the files that are gone, and would say "put back 0" as if it were the machine's answer.
  const fromMachine = live.data?.machine_source === "machine";
  const unread = !reading && !fromMachine;
  const preview = fromMachine ? live.data?.resume_preview : null;
  const lines = preview?.lines ?? [];
  const putBack = preview ? plural(preview.push, "profile") : "";
  const removal = preview && preview.remove > 0 ? ` and remove ${preview.remove}` : "";
  const question = reading
    ? "The machine looks reset: reading what resuming would do…"
    : unread
      ? "The machine looks reset, and could not be read just now, so what resuming would do is not known."
      : preview
        ? `The machine looks reset: put back ${putBack}${removal}?`
        : "The machine looks reset: syncs write nothing until you say so.";
  const button = preview
    ? preview.push === 0 && preview.remove === 0
      ? "Resume syncing: the next sync changes nothing"
      : `Resume syncing: the next sync puts back ${putBack}${preview.remove > 0 ? ` and removes ${preview.remove}` : ""}`
    : "Resume syncing";

  return (
    <div
      className="space-y-2 rounded-md border border-status-warn/40 bg-status-warn/10 p-3"
      data-testid="reset-banner"
      data-reading={reading ? "yes" : "no"}
    >
      <p className="flex items-start gap-1.5 font-medium text-sm text-status-warn-text">
        <AlertTriangle className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
        <span data-testid="reset-question">{question}</span>
      </p>
      <p className="text-status-warn-text text-xs">
        None of the profiles the last sync put on the machine are there any more, so a sync writes
        nothing until you resume it. This stops a sync from refilling a machine somebody wiped, or
        from writing to the wrong machine.
      </p>
      {lines.length > 0 ? (
        <div>
          <button
            type="button"
            className="flex items-center gap-1 text-status-warn-text text-xs underline underline-offset-2"
            data-testid="reset-lines-toggle"
            onClick={() => setShowLines((open) => !open)}
          >
            <ChevronRight
              className={cn("size-3 transition-transform", showLines ? "rotate-90" : "")}
              aria-hidden="true"
            />
            {showLines ? "Hide what it would do" : "What it would do"}
          </button>
          {showLines ? (
            <ul
              className="mt-1 list-disc space-y-0.5 pl-5 text-status-warn-text text-xs"
              data-testid="reset-lines"
            >
              {lines.map((line) => (
                <li
                  key={`${line.kind}-${line.row_id ?? "x"}-${line.device_id ?? line.label}`}
                  className="break-words"
                >
                  {previewLine(line)}
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}
      <Button
        size="sm"
        variant="outline"
        className="h-auto min-h-8 max-w-full whitespace-normal text-left"
        disabled={resume.isPending}
        data-testid="reset-resume"
        onClick={() => once((release) => resume.mutate(undefined, { onSettled: release }))}
      >
        {button}
      </Button>
    </div>
  );
}
