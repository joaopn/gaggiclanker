import { Check, Search, X } from "lucide-react";
import { Link } from "react-router-dom";
import { ApiClientError } from "@/api/client";
import type { PatternProposal, PatternRun, PatternSkipped, PatternSource } from "@/api/types";
import { SectionCard } from "@/components/layout/SectionCard";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useBeans, useGrinders } from "@/hooks/useCatalog";
import { useAnswerPatternProposal, usePatterns, useStartPatternRun } from "@/hooks/usePatterns";
import { useQueryErrorToast } from "@/hooks/useQueryErrorToast";
import { useSingleFlight } from "@/hooks/useSingleFlight";
import { attempt } from "@/lib/mutations";

/**
 * Find patterns across Sets: one pressed call that proposes general insights.
 *
 * A Set's insight reaches that Set alone, so a lesson that is true across coffees is learned
 * again, Set by Set. This section is the person's way of asking for it to be generalised: the
 * button starts **one** call that reads every Set's confirmed insights, and what comes back are
 * proposals, each with the Set insights it was derived from. Nothing changes until a proposal
 * is **approved**, and approving **deletes** the Set insights it names (the general insight
 * replaces them, scoped so it reaches every one of those Sets) and, when it says so, the general
 * insight it replaces. It never runs by itself: this button is the only way.
 */
export function PatternsSection() {
  const patterns = usePatterns();
  const start = useStartPatternRun();
  const singleFlight = useSingleFlight();
  useQueryErrorToast(patterns.error, "Could not load the pattern finder");

  if (patterns.isPending) {
    return (
      <SectionCard title="Find patterns across Sets" description="Loading.">
        <Skeleton className="h-16 w-full" />
      </SectionCard>
    );
  }
  const data = patterns.data;
  if (!data) {
    return (
      <SectionCard title="Find patterns across Sets">
        <p className="text-muted-foreground text-sm">The pattern finder could not be loaded.</p>
      </SectionCard>
    );
  }

  const run = data.run ?? null;
  const proposals = data.proposals ?? [];
  const running = run?.status === "running" || start.isPending;
  const enough = data.sets_with_insights >= data.min_sets;

  return (
    <SectionCard
      title="Find patterns across Sets"
      description="Reads the confirmed insights of every Set and proposes general insights that several Sets say in different words. Nothing is written until you approve a proposal, and approving one deletes the Set insights it came from."
      actions={
        <Button
          size="sm"
          disabled={running || !enough}
          data-testid="find-patterns"
          onClick={() =>
            singleFlight((release) => void attempt(() => start.mutateAsync()).finally(release))
          }
        >
          <Search className="size-3.5" aria-hidden="true" />
          {running ? "Finding patterns…" : "Find patterns"}
        </Button>
      }
    >
      <div className="space-y-3">
        <p className="text-muted-foreground text-sm" data-testid="patterns-since">
          {sinceSentence(
            data.new_since_last_run,
            data.sets_with_insights,
            data.min_sets,
            run,
            data.counted_from ?? null,
          )}
        </p>
        <RunState run={run} proposals={proposals} />
        {proposals.length > 0 ? (
          <ul className="space-y-2" data-testid="pattern-proposals">
            {proposals.map((proposal) => (
              <PatternCard key={proposal.id} proposal={proposal} runGoing={running} />
            ))}
          </ul>
        ) : null}
      </div>
    </SectionCard>
  );
}

/** The line under the heading: why the button is off, or how much is new. */
export function sinceSentence(
  fresh: number,
  sets: number,
  minimum: number,
  run: PatternRun | null,
  countedFrom: string | null,
): string {
  if (sets < minimum) {
    return `Needs confirmed insights in at least ${minimum} Sets, and ${
      sets === 0 ? "none have" : sets === 1 ? "one Set has" : `${sets} Sets have`
    } them. A Set's insights are added in its conversations.`;
  }
  const noun = fresh === 1 ? "insight" : "insights";
  if (countedFrom === null) {
    const reference = run === null ? "No run yet" : "No run has finished yet";
    return `${reference}: ${fresh} confirmed Set ${noun} across ${sets} Sets to read.`;
  }
  // A failed or running run after a finished one does not move the line: the count is from the
  // run that finished, and the sentence says so rather than calling it the last run.
  const reference = run?.status === "done" ? "the last run" : "the last run that finished";
  return `${fresh} confirmed Set ${noun} ${fresh === 1 ? "has" : "have"} arrived since ${reference}.`;
}

/** What the newest run is doing, or how it ended, in words. */
function RunState({ run, proposals }: { run: PatternRun | null; proposals: PatternProposal[] }) {
  if (run === null) return null;
  if (run.status === "running") {
    return (
      <p className="text-sm" role="status" data-testid="patterns-running">
        Reading {run.insights_read} Set insights from {run.sets_read} Sets. This takes a minute or
        so; the proposals appear here when it is done.
      </p>
    );
  }
  if (run.status === "failed") {
    return (
      <p role="alert" className="text-destructive text-sm" data-testid="patterns-failed">
        The last run failed: {run.error ?? "no reason was given"}. Nothing was changed
        {proposals.length > 0 ? ", and the proposals from the run before it are still below" : ""}.
      </p>
    );
  }
  if (run.status === "interrupted") {
    return (
      <p className="text-sm" data-testid="patterns-interrupted">
        The last run was interrupted when the app stopped. Nothing was changed; press Find patterns
        to run it again.
      </p>
    );
  }
  const dropped = droppedWords(run);
  return (
    <div className="space-y-1">
      {proposals.length === 0 ? (
        <p className="text-sm" data-testid="patterns-none">
          The last run read {run.insights_read} Set insights from {run.sets_read} Sets and found
          nothing worth proposing.
        </p>
      ) : null}
      {dropped ? (
        <p className="text-muted-foreground text-xs" data-testid="patterns-dropped">
          {dropped}
        </p>
      ) : null}
    </div>
  );
}

const DROP_WORDS: Record<string, string> = {
  invented_source: "named an insight it was not given",
  invented_replaces: "replaced an insight it was not given",
  one_set: "rested on one Set",
  profile_style: "was scoped by profile style",
  scope_not_shared: "had a scope its sources do not all share",
};

/** What the checks refused, so a run that proposed nothing is not a mystery. */
export function droppedWords(run: PatternRun): string | null {
  const entries = Object.entries(run.dropped ?? {}).filter(([, count]) => count > 0);
  if (entries.length === 0) return null;
  const parts = entries.map(
    ([reason, count]) => `${count} ${DROP_WORDS[reason] ?? reason.replaceAll("_", " ")}`,
  );
  return `${run.proposals_dropped} proposal${run.proposals_dropped === 1 ? " was" : "s were"} dropped before you saw ${
    run.proposals_dropped === 1 ? "it" : "them"
  }: ${parts.join("; ")}.`;
}

/** What the scope's ids are called, read from the beans and grinders the person has. */
export type CatalogueState = "loading" | "loaded" | "failed";

export type ScopeNames = {
  beans: ReadonlyMap<number, string>;
  grinders: ReadonlyMap<number, string>;
  /**
   * How each catalogue stands. While one loads an id is unknown, not gone ("bean …"); once it
   * has loaded without the id the thing is gone; if it failed to load there is nothing to say
   * but the id.
   */
  loaded?: { beans: CatalogueState; grinders: CatalogueState };
};

const SCOPE_LABELS: Record<string, string> = {
  roast_level: "roast level",
  process: "process",
  origin: "origin",
};

/**
 * The scope in words: which Sets the general insight would reach.
 *
 * A bean and a grinder are named ("grinder Niche Zero"); an id is shown only when the thing
 * is gone, which is the one case where there is no name to give.
 */
export function scopeWords(scope: PatternProposal["scope"], names?: ScopeNames): string {
  const entries = Object.entries((scope ?? {}) as Record<string, unknown>).filter(
    ([, value]) => value !== null && value !== undefined,
  );
  if (entries.length === 0) return "Applies to every Set.";
  const parts = entries.map(([key, value]) => {
    if (key === "bean_id" || key === "grinder_id") {
      const kind = key === "bean_id" ? "bean" : "grinder";
      const found = (key === "bean_id" ? names?.beans : names?.grinders)?.get(Number(value));
      if (found) return `${kind} ${found}`;
      // "No longer there" is a claim about the catalogue, so it is made only once the
      // catalogue has loaded and lacks the item; while it loads the name is just not here yet.
      const loaded = key === "bean_id" ? names?.loaded?.beans : names?.loaded?.grinders;
      if (loaded === "loading") return `${kind} …`;
      if (loaded === "failed") return `${kind} #${String(value)}`;
      return `${kind} #${String(value)} (no longer there)`;
    }
    return `${SCOPE_LABELS[key] ?? key.replaceAll("_", " ")} ${String(value)}`;
  });
  return `Applies to Sets with ${parts.join(", ")}.`;
}

/** A failed press, said in words the person can act on. */
export function patternFailure(error: Error): string {
  if (error instanceof ApiClientError) {
    if (error.code === "PATTERN_TOO_FEW_SETS") {
      return "The Set insights this was derived from have changed since it was proposed, and fewer than two Sets' are left, so nothing was written. Dismiss it.";
    }
    if (error.code === "PATTERNS_RUNNING") {
      return "A run is going and replaces these proposals when it finishes. Answer them once it has.";
    }
    if (error.code === "PATTERN_PROPOSAL_DECIDED") {
      return "This proposal was already answered, in another tab or because a newer run replaced it. The card will show how once it refreshes.";
    }
  }
  return error.message;
}

const SKIP_WORDS: Record<PatternSkipped["reason"], string> = {
  gone: "was already gone",
  not_confirmed: "was taken back since, so it stays",
  scope_changed: "belongs to a Set the scope no longer reaches, so it stays",
  replaced_gone: "was already gone",
};

/** The proposal's sources grouped by Set, in the order the Sets first appear. */
export function groupBySet(sources: PatternSource[]) {
  const groups = new Map<number, { setId: number; name: string; items: PatternSource[] }>();
  for (const source of sources) {
    const group = groups.get(source.set_id);
    if (group) group.items.push(source);
    else
      groups.set(source.set_id, { setId: source.set_id, name: source.set_name, items: [source] });
  }
  return [...groups.values()];
}

function catalogueState(query: { isSuccess: boolean; isError: boolean }): CatalogueState {
  return query.isSuccess ? "loaded" : query.isError ? "failed" : "loading";
}

function PatternCard({ proposal, runGoing }: { proposal: PatternProposal; runGoing: boolean }) {
  const answer = useAnswerPatternProposal();
  const beans = useBeans(true);
  const grinders = useGrinders();
  const names: ScopeNames = {
    beans: new Map((beans.data?.items ?? []).map((item) => [item.id, item.name])),
    grinders: new Map((grinders.data?.items ?? []).map((item) => [item.id, item.name])),
    loaded: { beans: catalogueState(beans), grinders: catalogueState(grinders) },
  };
  const singleFlight = useSingleFlight();

  // What the server said to the last press on this card, before the read it invalidated is back.
  const decision =
    answer.variables?.id === proposal.id && !answer.isPending ? (answer.data ?? null) : null;
  const shown = decision?.proposal ?? proposal;
  const waiting = shown.status === "proposed";
  const skipped = decision?.skipped ?? shown.skipped ?? [];
  const sources = shown.sources ?? [];
  const failure = answer.variables?.id === proposal.id && answer.error ? answer.error : null;
  const skippedIds = new Set(
    skipped.filter((item) => item.set_id !== null).map((item) => item.insight_id),
  );
  const deleted = sources.filter((source) => !skippedIds.has(source.insight_id));
  const groups = groupBySet(sources);
  const replacedGone = skipped.some((item) => item.reason === "replaced_gone");

  function send(choice: "approve" | "dismiss") {
    singleFlight((release) => {
      void attempt(() => answer.mutateAsync({ id: proposal.id, answer: choice })).finally(release);
    });
  }

  return (
    <li
      className="rounded-lg border border-primary/40 bg-primary/5 p-3"
      data-testid="pattern-proposal"
      data-proposal={proposal.id}
      data-status={shown.status}
    >
      <p className="font-medium text-sm" data-testid="pattern-text">
        {shown.text}
      </p>
      <p className="mt-0.5 text-muted-foreground text-xs" data-testid="pattern-scope">
        {scopeWords(shown.scope, names)}
      </p>

      <div className="mt-2 space-y-1.5" data-testid="pattern-sources">
        <p className="text-muted-foreground text-xs">Derived from</p>
        {groups.map((group) => (
          <div key={group.setId} data-testid="pattern-source-set">
            <Link to={`/sets/${group.setId}`} className="font-medium text-xs underline">
              {group.name}
            </Link>
            <ul className="ml-4 list-disc text-sm">
              {group.items.map((source) => (
                <li key={source.insight_id} data-testid="pattern-source">
                  {source.text}
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>

      {waiting ? (
        <>
          <p className="mt-2 text-sm" data-testid="pattern-consequence">
            Approving deletes these {sources.length} Set insights
            {shown.replaces_text ? ", and replaces this general insight (it is deleted too)" : ""}.
          </p>
          {shown.replaces_text ? (
            <p className="mt-1 text-muted-foreground text-sm" data-testid="pattern-replaces">
              Replaces: {shown.replaces_text}
            </p>
          ) : null}
          <div className="mt-2 flex flex-wrap gap-2">
            <Button
              size="sm"
              disabled={answer.isPending || runGoing}
              onClick={() => send("approve")}
            >
              <Check className="size-3.5" aria-hidden="true" />
              Approve
            </Button>
            <Button
              size="sm"
              variant="outline"
              disabled={answer.isPending || runGoing}
              onClick={() => send("dismiss")}
            >
              <X className="size-3.5" aria-hidden="true" />
              Dismiss
            </Button>
          </div>
          {runGoing ? (
            <p className="mt-1 text-muted-foreground text-xs" data-testid="pattern-run-going">
              A run is reading the insights now and replaces these proposals when it finishes, so
              they can be answered once it has.
            </p>
          ) : null}
        </>
      ) : (
        <div className="mt-2 space-y-1 text-sm" data-testid="pattern-decided">
          {shown.status === "approved" ? (
            <>
              <p>
                Approved: the general insight is on this page, and {deleted.length} Set insight
                {deleted.length === 1 ? " was" : "s were"} deleted.
              </p>
              {skipped.length > 0 ? (
                <ul className="ml-4 list-disc text-muted-foreground" data-testid="pattern-skipped">
                  {skipped.map((item) => (
                    <li key={`${item.reason}-${item.insight_id ?? "replaced"}`}>
                      {item.reason === "replaced_gone"
                        ? "The general insight it was to replace "
                        : "A Set insight, "}
                      {item.text ? `"${item.text}" ` : ""}
                      {SKIP_WORDS[item.reason]}.
                    </li>
                  ))}
                </ul>
              ) : null}
              {replacedGone ? null : shown.replaces_text ? (
                <p className="text-muted-foreground">
                  The general insight it replaced was deleted.
                </p>
              ) : null}
            </>
          ) : shown.status === "dismissed" ? (
            <p>Dismissed: nothing was changed, and the next run is told you declined it.</p>
          ) : (
            <p>A newer run replaced this proposal.</p>
          )}
        </div>
      )}

      {failure ? (
        <p role="alert" className="mt-2 text-destructive text-sm" data-testid="pattern-error">
          {patternFailure(failure)}
        </p>
      ) : null}
    </li>
  );
}
