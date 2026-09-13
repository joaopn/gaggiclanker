import { AlertTriangle, Loader2, Sparkles } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import type { ShotListRow } from "@/api/types";
import { Button } from "@/components/ui/button";
import { useRunAnalysis } from "@/hooks/useAnalysis";
import { attempt } from "@/lib/mutations";
import { ANALYSIS_ANCHOR } from "@/lib/shots";
import { cn } from "@/lib/utils";

/**
 * The Analyse column: where the newest analysis of a shot got to, and the
 * button that starts one.
 *
 * It refuses on exactly what the shot page refuses on and nothing more. The
 * page hides its analysis panel for a quarantined shot, and the server answers
 * 422 for one, because unparsed bytes have no diagnostics to analyse — so
 * that is the one disabled state, with the reason in its title. A shot with no
 * Set is analysed on the page with a warning rather than refused, so here it
 * is a live button whose title carries the same warning. Anything else that
 * can go wrong (no provider configured, a rate limit) the page finds out by
 * trying, and so does this: the server records a `failed` row, the toast says
 * why, and the cell turns into Retry.
 *
 * **The in-flight state is the row's**, as on the page: `analysis.started`,
 * `finished` and `failed` refresh the list through `EVENT_INVALIDATIONS`, so a
 * run started from another tab or by a Set batch shows here, and a run started
 * here flips to Analysed when it lands.
 *
 * **One click is one analysis.** An analysis is a paid call. The mutation's own
 * `isPending` arrives a render late — a double click's second event lands
 * before it — so a ref guards the handler synchronously. After the 202 the
 * button stays "Analysing…" until the list has been re-read and the row's state
 * has moved, rather than flashing back to Analyse in between and inviting a
 * second press. The server would answer that press with the running row
 * instead of a second call, but the button should not rely on it.
 */
export function AnalyseCell({ shot, className }: { shot: ShotListRow; className?: string }) {
  const run = useRunAnalysis();
  const inFlight = useRef(false);
  // The row state this cell sent a request from; `null` when nothing is sent.
  const [sentFrom, setSentFrom] = useState<string | null>(null);
  const state = shot.analysis_state;
  const name = `shot ${shot.device_id}`;

  // The row moved: the list has caught up with what was sent.
  useEffect(() => {
    if (sentFrom !== null && state !== sentFrom) setSentFrom(null);
  }, [state, sentFrom]);

  async function analyse() {
    if (inFlight.current || sentFrom !== null) return;
    inFlight.current = true;
    const from = state;
    setSentFrom(from);
    // `force` for a re-run, as the page sends it once a shot has any analysis:
    // without it the server hands back a previous `ok` rather than running.
    const result = await attempt(() =>
      run.mutateAsync({ shotId: shot.id, force: from !== "none" }),
    );
    inFlight.current = false;
    // Nothing is going to move the row — the request failed, or the server
    // answered with a row in the state this one is already in (a retry that
    // failed again at once) — so stop waiting for it.
    if (result === undefined || result.status === from) setSentFrom(null);
  }

  const base = cn("h-7 gap-1 px-2 text-xs", className);

  if (shot.quarantined) {
    const reason = "Quarantined: its bytes never parsed, so there are no diagnostics to analyse";
    return (
      // The title is on a wrapper as well as on the button: a disabled button
      // receives no pointer events, and some browsers show no title for one.
      <span title={reason} data-testid="analyse-cell" data-state="unavailable">
        <Button
          variant="outline"
          size="xs"
          className={base}
          disabled
          aria-label={`Analyse ${name}`}
          aria-description={reason}
        >
          <Sparkles aria-hidden="true" />
          Analyse
        </Button>
      </span>
    );
  }

  if (state === "running" || sentFrom !== null) {
    return (
      <span
        data-testid="analyse-cell"
        data-state="running"
        role="status"
        className={cn("inline-flex items-center gap-1 text-muted-foreground text-xs", className)}
      >
        <Loader2 className="size-3 animate-spin" aria-hidden="true" />
        Analysing…
      </span>
    );
  }

  if (state === "ok") {
    return (
      <Link
        to={`/shots/${shot.id}#${ANALYSIS_ANCHOR}`}
        data-testid="analyse-cell"
        data-state="ok"
        aria-label={`Analysed: open the analysis of ${name}`}
        className={cn(
          "inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs hover:bg-muted",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
          className,
        )}
      >
        <Sparkles className="size-3" aria-hidden="true" />
        Analysed
      </Link>
    );
  }

  if (state === "failed") {
    const why = shot.analysis_error
      ? `The last analysis failed: ${shot.analysis_error}`
      : "The last analysis failed";
    return (
      <span data-testid="analyse-cell" data-state="failed">
        <Button
          variant="outline"
          size="xs"
          className={cn(base, "text-status-warn-text")}
          title={why}
          aria-label={`Retry the analysis of ${name}`}
          aria-description={why}
          onClick={() => void analyse()}
        >
          <AlertTriangle aria-hidden="true" />
          Retry
        </Button>
      </span>
    );
  }

  const warning = shot.set_badge
    ? undefined
    : "Not in a Set: there is no bean, grinder or recipe to reason from, and the analysis will say so";
  return (
    <span data-testid="analyse-cell" data-state="none">
      <Button
        variant="outline"
        size="xs"
        className={base}
        title={warning}
        aria-label={`Analyse ${name}`}
        onClick={() => void analyse()}
      >
        <Sparkles aria-hidden="true" />
        Analyse
      </Button>
    </span>
  );
}
