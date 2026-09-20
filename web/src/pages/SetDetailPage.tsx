import { AlertTriangle, Archive, ArrowLeft, Coffee, GitBranch, Sparkles } from "lucide-react";
import { useId, useState } from "react";
import { Link, useParams } from "react-router-dom";
import type { Suggestion } from "@/api/types";
import { SuggestionCard } from "@/components/analysis/SuggestionCard";
import { SetTrendChart } from "@/components/charts/SetTrendChart";
import { DiscussButton } from "@/components/chat/DiscussButton";
import { InsightCard } from "@/components/knowledge/InsightCard";
import { EmptyState } from "@/components/layout/EmptyState";
import { PageHeader } from "@/components/layout/PageHeader";
import { SectionCard } from "@/components/layout/SectionCard";
import { VersionTimeline } from "@/components/sets/VersionTimeline";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useAnalyseSet, useSetSuggestions } from "@/hooks/useAnalysis";
import { useKnowledgeInsights } from "@/hooks/useKnowledge";
import { useQueryErrorToast } from "@/hooks/useQueryErrorToast";
import {
  useActivateSet,
  useAddSetVersion,
  useArchiveSet,
  useSet,
  useSetTrends,
} from "@/hooks/useSets";
import { attempt } from "@/lib/mutations";
import { grindPatch, setSummary, versionSummary } from "@/lib/sets";
import { cn } from "@/lib/utils";

/**
 * What this archive has learned that applies to this Set.
 *
 * Selected by the server through the same `select_insights` an analysis of one
 * of these shots is given, so the page cannot show a different answer from the
 * prompt — which is the whole reason the filter is a query parameter rather than
 * a scope comparison written a second time in TypeScript.
 *
 * Confirmed only, and the card's unconfirm button is live: taking an insight
 * back out is meant to be as easy as it was to put in, because the one that
 * turns out to be wrong is discovered by reading an analysis that followed it.
 * Renders nothing when there is nothing — an empty card on every Set would be
 * noise on the page people look at most.
 */
function SetInsights({ setId }: { setId: number }) {
  const insights = useKnowledgeInsights({ set_id: setId });
  const items = insights.data?.items ?? [];
  if (items.length === 0) return null;
  return (
    <SectionCard
      title="What you have learned about this Set"
      description="Confirmed insights whose scope matches this bean, grinder and machine. Every analysis of a shot in this Set is told them, above the general rules."
    >
      <ul className="space-y-2" data-testid="set-insights">
        {items.map((insight) => (
          <InsightCard key={insight.id} insight={insight} compact />
        ))}
      </ul>
    </SectionCard>
  );
}

/**
 * One Set: what it is, how it has gone, and every recipe it has been through.
 *
 * The chart goes above the timeline because it is the answer to the question
 * that brought somebody here — "is this getting better" — and the timeline is
 * the explanation. Both read oldest-to-newest inside themselves and the
 * timeline reads newest-first between versions, which is the same convention as
 * the shots list.
 *
 * Code-split (`App.tsx` lazy-loads it) for the same reason the shot page is:
 * this is the only other route that needs Chart.js.
 */

const FIELD = cn(
  "h-8 w-full rounded-md border border-input bg-background px-2 text-sm",
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
);

export function SetDetailPage() {
  const params = useParams();
  const setId = Number.parseInt(params.setId ?? "", 10);
  // A route param that is not a number is a typed-in URL, and the query is
  // never enabled for it — so it has to be treated as "no such Set" here rather
  // than left as a pending query, which renders a skeleton that never resolves.
  const valid = Number.isFinite(setId);
  const detail = useSet(valid ? setId : undefined);
  const trends = useSetTrends(valid ? setId : undefined);
  const suggestions = useSetSuggestions(valid ? setId : undefined);
  const analyse = useAnalyseSet();
  const activate = useActivateSet();
  const archive = useArchiveSet();
  const [versioning, setVersioning] = useState(false);
  useQueryErrorToast(detail.error, "Could not load this Set");

  if (valid && detail.isPending) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-10 w-64" />
        <Skeleton className="h-56 w-full" />
      </div>
    );
  }

  if (!valid || detail.isError || !detail.data) {
    return (
      <div className="space-y-4">
        <BackLink />
        <EmptyState
          icon={AlertTriangle}
          title="No such Set"
          description={
            valid
              ? (detail.error?.message ?? "The archive has no Set with that id.")
              : "That is not a Set id."
          }
        />
      </div>
    );
  }

  const row = detail.data.set;
  const current = detail.data.versions[0]?.version;

  return (
    <div className="space-y-4">
      <BackLink />
      <PageHeader
        title={row.name}
        subtitle={setSummary(row)}
        actions={
          <div className="flex items-center gap-2">
            {row.active ? <Badge data-testid="set-active">active</Badge> : null}
            {row.status === "archived" ? <Badge variant="outline">archived</Badge> : null}
            {/* The scoped conversation this feature is for: the Set's recipe,
                its recent shots and the insights that apply are already in the
                prompt before the first word is typed. */}
            <DiscussButton
              setId={row.id}
              question={`How is ${row.name} going, and what should I change next?`}
            />
            {!row.active && row.status === "active" ? (
              <Button
                variant="outline"
                size="sm"
                disabled={activate.isPending}
                onClick={() => activate.mutate(row.id)}
              >
                <Coffee className="size-3.5" aria-hidden="true" />
                This is what is loaded
              </Button>
            ) : null}
            {row.status === "active" ? (
              <Button
                variant="ghost"
                size="sm"
                disabled={archive.isPending}
                onClick={() => archive.mutate(row.id)}
              >
                <Archive className="size-3.5" aria-hidden="true" />
                Archive
              </Button>
            ) : null}
          </div>
        }
      />

      <SectionCard
        title={`Now brewing: v${row.current_version_no}`}
        description={current ? versionSummary(current) : undefined}
        actions={
          <Button size="sm" variant="outline" onClick={() => setVersioning((open) => !open)}>
            <GitBranch className="size-3.5" aria-hidden="true" />
            Change something
          </Button>
        }
      >
        {versioning ? (
          <NewVersionForm setId={row.id} onDone={() => setVersioning(false)} />
        ) : (
          <p className="text-muted-foreground text-sm">
            {row.shot_count} shot{row.shot_count === 1 ? "" : "s"} across {row.version_count}{" "}
            version{row.version_count === 1 ? "" : "s"}. Changing the grind, the dose or the profile
            records a new version, so the archive can tell you what the change did.
          </p>
        )}
      </SectionCard>

      <SectionCard
        title="How it has gone"
        description="One point per shot, oldest first, with a dashed line wherever a new version began. The execution score and your rating share the left axis on purpose: a clean shot you did not like is the interesting case."
      >
        {trends.isPending ? (
          <Skeleton className="h-56 w-full" />
        ) : (trends.data?.shots.length ?? 0) === 0 ? (
          <p className="text-muted-foreground text-sm">
            No shots yet. The next one pulled with this Set's profile files itself here.
          </p>
        ) : (
          <SetTrendChart trends={trends.data as NonNullable<typeof trends.data>} />
        )}
      </SectionCard>

      <SetInsights setId={row.id} />

      <SectionCard
        title="Versions"
        description="Newest first, each showing what changed against the version it came from."
      >
        <VersionTimeline versions={detail.data.versions} judgements={detail.data.judgements} />
      </SectionCard>

      <SectionCard
        title="Suggestions"
        description="Every piece of advice an analysis has given about a shot in this Set, newest first, grouped by the version it was about. Accepting one records a new version; the acceptance history is the answer to 'did following the model help'."
        actions={
          <Button
            size="sm"
            variant="outline"
            disabled={analyse.isPending}
            data-testid="analyse-set"
            onClick={() => analyse.mutate({ setId: row.id })}
          >
            <Sparkles className="size-3.5" aria-hidden="true" />
            {analyse.isPending ? "Analysing…" : "Analyse the un-analysed"}
          </Button>
        }
      >
        {suggestions.isPending ? (
          <Skeleton className="h-24 w-full" />
        ) : (
          <SuggestionsByVersion
            items={suggestions.data?.items ?? []}
            versionNumbers={Object.fromEntries(
              detail.data.versions.map((entry) => [entry.version.id, entry.version.version_no]),
            )}
          />
        )}
      </SectionCard>
    </div>
  );
}

/**
 * The Set's advice, grouped by the version it was about.
 *
 * Grouped rather than flat because a suggestion is a delta from the numbers it
 * was given: advice about v1 and advice about v3 are not comparable, and a flat
 * list invites reading them as one conversation. Within a version they stay in
 * the order the server sent — newest analysis first, priority within it.
 */
function SuggestionsByVersion({
  items,
  versionNumbers,
}: {
  items: Suggestion[];
  versionNumbers: Record<number, number>;
}) {
  if (items.length === 0) {
    return (
      <p className="text-muted-foreground text-sm" data-testid="no-suggestions">
        No analysis has suggested anything for this Set yet.
      </p>
    );
  }

  const groups = new Map<number, Suggestion[]>();
  for (const item of items) {
    const key = item.set_version_id ?? 0;
    const bucket = groups.get(key);
    if (bucket) bucket.push(item);
    else groups.set(key, [item]);
  }

  return (
    <div className="space-y-4" data-testid="set-suggestions">
      {[...groups.entries()].map(([versionId, group]) => (
        <div key={versionId}>
          <h4 className="mb-1.5 text-muted-foreground text-xs">
            about v{versionNumbers[versionId] ?? "?"}
          </h4>
          <ul className="space-y-2">
            {group.map((item) => (
              <SuggestionCard key={item.id} suggestion={item} />
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}

function BackLink() {
  return (
    <Link
      to="/sets"
      className="inline-flex items-center gap-1 text-muted-foreground text-sm hover:text-foreground"
    >
      <ArrowLeft className="size-3.5" aria-hidden="true" />
      All Sets
    </Link>
  );
}

/**
 * A new version: only the fields you changed, plus what you are trying.
 *
 * Every field starts empty rather than prefilled with the current recipe, and
 * that is the whole design of the endpoint showing through: an omitted field
 * inherits the parent's value, so a form prefilled with the current values
 * would record all six as changed and the timeline's diff would say nothing.
 */
function NewVersionForm({ setId, onDone }: { setId: number; onDone: () => void }) {
  const add = useAddSetVersion();
  const [grind, setGrind] = useState("");
  const [dose, setDose] = useState("");
  const [target, setTarget] = useState("");
  const [temperature, setTemperature] = useState("");
  const [intent, setIntent] = useState("");
  const ids = {
    grind: useId(),
    dose: useId(),
    target: useId(),
    temperature: useId(),
    intent: useId(),
  };

  function number(value: string): number | undefined {
    const parsed = Number.parseFloat(value);
    return Number.isFinite(parsed) && parsed > 0 ? parsed : undefined;
  }

  return (
    <form
      data-testid="new-version-form"
      className="space-y-3"
      onSubmit={async (event) => {
        event.preventDefault();
        const version = await attempt(() =>
          add.mutateAsync({
            setId,
            patch: {
              intent,
              prediction: "",
              origin: "manual",
              ...grindPatch(grind),
              ...(number(dose) ? { dose_g: number(dose) } : {}),
              ...(number(target) ? { target_yield_g: number(target) } : {}),
              ...(number(temperature) ? { target_temperature_c: number(temperature) } : {}),
            },
          }),
        );
        if (version) onDone();
      }}
    >
      <p className="text-muted-foreground text-xs">
        Fill in only what changed. Anything left blank carries over from the current version.
      </p>
      <div className="grid gap-3 sm:grid-cols-4">
        <Labelled id={ids.grind} label="Grind">
          <input
            id={ids.grind}
            className={FIELD}
            value={grind}
            onChange={(event) => setGrind(event.target.value)}
          />
        </Labelled>
        <Labelled id={ids.dose} label="Dose (g)">
          <input
            id={ids.dose}
            className={FIELD}
            inputMode="decimal"
            value={dose}
            onChange={(event) => setDose(event.target.value)}
          />
        </Labelled>
        <Labelled id={ids.target} label="Target yield (g)">
          <input
            id={ids.target}
            className={FIELD}
            inputMode="decimal"
            value={target}
            onChange={(event) => setTarget(event.target.value)}
          />
        </Labelled>
        <Labelled id={ids.temperature} label="Temperature (°C)">
          <input
            id={ids.temperature}
            className={FIELD}
            inputMode="decimal"
            value={temperature}
            onChange={(event) => setTemperature(event.target.value)}
          />
        </Labelled>
      </div>
      <Labelled id={ids.intent} label="What are you trying?">
        <input
          id={ids.intent}
          required
          className={FIELD}
          placeholder="one click finer, chasing the sourness out"
          value={intent}
          onChange={(event) => setIntent(event.target.value)}
        />
      </Labelled>
      <div className="flex gap-2">
        <Button type="submit" size="sm" disabled={add.isPending}>
          {add.isPending ? "Recording…" : "Record the version"}
        </Button>
        <Button type="button" size="sm" variant="ghost" onClick={onDone}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

function Labelled({
  id,
  label,
  children,
}: {
  id: string;
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <label htmlFor={id} className="mb-1 block text-muted-foreground text-xs">
        {label}
      </label>
      {children}
    </div>
  );
}
