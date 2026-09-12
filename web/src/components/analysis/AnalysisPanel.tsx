import { AlertTriangle, BookOpen, Sparkles } from "lucide-react";
import { useId, useState } from "react";
import { Link } from "react-router-dom";
import type { Analysis, AnalysisOutput } from "@/api/types";
import { SuggestionCard } from "@/components/analysis/SuggestionCard";
import { SectionCard } from "@/components/layout/SectionCard";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useRunAnalysis } from "@/hooks/useAnalysis";
import { formatTime } from "@/lib/shots";
import { cn } from "@/lib/utils";

/**
 * What the model made of this shot, and the buttons that act on it.
 *
 * The panel renders the newest analysis and keeps the earlier ones behind a
 * disclosure, because a re-run is normally a correction of the previous reading
 * rather than a second opinion to weigh — but the previous reading is exactly
 * what you want when the correction surprises you.
 *
 * **In-flight state comes from the row, not from the mutation.** The server
 * queues the work and answers 202 with a `running` row, so "is this shot being
 * analysed" is a fact about the row rather than about whether *this* tab
 * pressed the button — which is what makes a run started by another tab, or by
 * a Set batch, show up here. `analysis.started` / `finished` / `failed` on the
 * LLM stream are what refresh it: `EVENT_INVALIDATIONS` maps all three onto the
 * analyses and shots keys, so the row is re-read within a frame of the server
 * moving it and no second EventSource is opened for this page.
 *
 * The earlier version watched the live-call list and disabled the button while
 * *any* analysis call was running anywhere, so one Set batch froze the button on
 * every other shot's page.
 */
export function AnalysisPanel({
  shotId,
  analyses,
  hasSet,
}: {
  shotId: number;
  analyses: Analysis[];
  /** A shot with no Set has no recipe to suggest changes to; say so up front. */
  hasSet: boolean;
}) {
  const run = useRunAnalysis();
  const [model, setModel] = useState("");
  const [showOlder, setShowOlder] = useState(false);
  const modelId = useId();

  const latest = analyses[0];
  const older = analyses.slice(1);
  // This shot's row, and only this shot's. A batch analysing forty others must
  // not disable the button here.
  const running = latest?.status === "running";

  return (
    <SectionCard
      title="Analysis"
      description="One structured call per run: the diagnostics, this Set, the shots before it, your verdict and the matching knowledge rules go in; a diagnosis and prioritised suggestions come out. The execution score above stays authoritative — this explains it."
      actions={
        <div className="flex items-center gap-2">
          <input
            id={modelId}
            className="h-8 w-36 rounded-md border border-input bg-background px-2 text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            placeholder="model (optional)"
            aria-label="Model override"
            value={model}
            onChange={(event) => setModel(event.target.value)}
          />
          <Button
            size="sm"
            disabled={run.isPending || running}
            data-testid="run-analysis"
            onClick={() =>
              run.mutate({ shotId, model: model || undefined, force: analyses.length > 0 })
            }
          >
            <Sparkles
              className={cn("size-3.5", running ? "animate-pulse" : undefined)}
              aria-hidden="true"
            />
            {run.isPending || running
              ? "Thinking…"
              : analyses.length > 0
                ? "Analyse again"
                : "Analyse this shot"}
          </Button>
        </div>
      }
    >
      {!hasSet ? (
        <p className="mb-3 rounded-md border border-border bg-muted/50 p-2 text-muted-foreground text-sm">
          This shot is not in a Set, so there is no bean, no grinder and no intended recipe to
          reason from. The analysis will say so, and no suggestion can be recorded as a version.
        </p>
      ) : null}

      {latest === undefined ? (
        <p className="text-muted-foreground text-sm" data-testid="analysis-empty">
          Not analysed yet.
        </p>
      ) : (
        <AnalysisBody analysis={latest} />
      )}

      {older.length > 0 ? (
        <div className="mt-4 border-border border-t pt-3">
          <button
            type="button"
            className="text-muted-foreground text-xs underline underline-offset-2"
            onClick={() => setShowOlder((open) => !open)}
          >
            {showOlder ? "Hide" : "Show"} {older.length} earlier{" "}
            {older.length === 1 ? "analysis" : "analyses"}
          </button>
          {showOlder ? (
            <ul className="mt-3 space-y-3" data-testid="older-analyses">
              {older.map((analysis) => (
                <li key={analysis.id} className="rounded-lg border border-border p-3">
                  <AnalysisBody analysis={analysis} compact />
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}
    </SectionCard>
  );
}

function AnalysisBody({ analysis, compact = false }: { analysis: Analysis; compact?: boolean }) {
  if (analysis.status === "running") {
    return (
      <p className="text-muted-foreground text-sm" data-testid="analysis-running">
        Running since {formatTime(analysis.created_at)} on {analysis.model || "the default model"}.
      </p>
    );
  }
  if (analysis.status !== "ok") {
    return <FailedAnalysis analysis={analysis} />;
  }

  const output = (analysis.output ?? {}) as AnalysisOutput;
  const suggestions = analysis.suggestions ?? [];

  return (
    <div className="space-y-4" data-testid="analysis-result">
      <Provenance analysis={analysis} />

      {output.diagnosis ? <p className="text-sm">{output.diagnosis}</p> : null}

      <div className="flex flex-wrap items-center gap-2">
        {output.shot_style ? <Badge variant="secondary">{output.shot_style}</Badge> : null}
        {output.taste_prediction ? (
          <Badge variant="outline" data-testid="taste-prediction">
            predicts {output.taste_prediction.balance}, {output.taste_prediction.body} body
            {output.taste_prediction.confidence ? ` (${output.taste_prediction.confidence})` : ""}
          </Badge>
        ) : null}
      </div>

      {output.execution ? (
        <div>
          <h4 className="mb-1 font-medium text-sm">Execution</h4>
          <p className="text-sm">{output.execution.summary}</p>
          {(output.execution.issues ?? []).length > 0 ? (
            <ul className="mt-1.5 space-y-1" data-testid="execution-issues">
              {(output.execution.issues ?? []).map((issue) => (
                <li key={`${issue.signal}-${issue.evidence}`} className="text-sm">
                  <span className="text-muted-foreground">{issue.severity}</span>{" "}
                  <span className="font-mono text-xs">{issue.signal}</span> — {issue.evidence}
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}

      {suggestions.length > 0 ? (
        <div>
          <h4 className="mb-1.5 font-medium text-sm">What to change</h4>
          <ul className="space-y-2" data-testid="suggestions">
            {suggestions.map((suggestion) => (
              <SuggestionCard key={suggestion.id} suggestion={suggestion} />
            ))}
          </ul>
        </div>
      ) : null}

      {(output.profile_patch ?? []).length > 0 ? (
        <div>
          <h4 className="mb-1 font-medium text-sm">Profile changes it would make</h4>
          {/* Recorded, never applied: the prototype writes nothing to the
              machine. Saying so here is what stops somebody waiting for a
              button that is not coming. */}
          <p className="mb-1.5 text-muted-foreground text-xs">
            Shown for reference. gaggiclanker writes nothing to the machine, so these are edits to
            make there yourself.
          </p>
          <ul className="space-y-1" data-testid="profile-patch">
            {(output.profile_patch ?? []).map((patch) => (
              <li key={`${patch.phase_index}-${patch.field}`} className="text-sm">
                <span className="font-mono text-xs">
                  phase {patch.phase_index}.{patch.field}
                </span>{" "}
                {patch.from} → {patch.to}
                {patch.reason ? ` — ${patch.reason}` : ""}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {(output.questions_for_user ?? []).length > 0 ? (
        <div>
          <h4 className="mb-1 font-medium text-sm">It would like to know</h4>
          <ul className="list-disc space-y-0.5 pl-4" data-testid="questions">
            {(output.questions_for_user ?? []).map((question) => (
              <li key={question} className="text-sm">
                {question}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {!compact && (output.rules_used ?? []).length > 0 ? (
        <div>
          <h4 className="mb-1 flex items-center gap-1.5 font-medium text-sm">
            <BookOpen className="size-3.5" aria-hidden="true" />
            Rules it leaned on
          </h4>
          {/* Every key here was checked against the rules this shot was
              actually given; an invented one is dropped server-side. The links
              are how a rule that misleads gets found and turned off. */}
          <ul className="flex flex-wrap gap-1.5" data-testid="rules-used">
            {(output.rules_used ?? []).map((key) => (
              <li key={key}>
                <Link
                  to={`/knowledge?rule=${encodeURIComponent(key)}`}
                  className="inline-flex rounded-full border border-border px-2 py-0.5 font-mono text-xs hover:bg-accent"
                >
                  {key}
                </Link>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

function FailedAnalysis({ analysis }: { analysis: Analysis }) {
  return (
    <div className="rounded-md border border-border bg-muted/50 p-3" data-testid="analysis-failed">
      <p className="flex items-center gap-1.5 font-medium text-sm">
        <AlertTriangle className="size-3.5 text-status-warn-text" aria-hidden="true" />
        {analysis.status === "interrupted"
          ? "Interrupted — the process stopped before this finished"
          : "That analysis did not complete"}
      </p>
      {analysis.error ? (
        <p className="mt-1 font-mono text-muted-foreground text-xs">{analysis.error}</p>
      ) : null}
      <p className="mt-1.5 text-muted-foreground text-xs">
        The row is kept so the failure is on the record. A rate limit clears from Settings; an auth
        error needs a key.
      </p>
    </div>
  );
}

function Provenance({ analysis }: { analysis: Analysis }) {
  const usage = analysis.usage as { total_tokens?: number | null } | null | undefined;
  return (
    <p className="text-muted-foreground text-xs" data-testid="analysis-provenance">
      {formatTime(analysis.created_at)} · {analysis.provider || "unknown provider"}
      {analysis.model ? ` · ${analysis.model}` : ""}
      {usage?.total_tokens ? ` · ${usage.total_tokens} tokens` : ""}
      {analysis.prompt_version ? ` · prompt ${analysis.prompt_version}` : ""}
    </p>
  );
}
