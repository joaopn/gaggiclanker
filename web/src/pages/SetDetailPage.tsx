import {
  AlertTriangle,
  Archive,
  ArrowLeft,
  Coffee,
  GitBranch,
  Sparkles,
  Trash2,
  Undo2,
} from "lucide-react";
import { useId, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ApiClientError, largeBatchCount } from "@/api/client";
import type { SetDetailData, SetRow, Suggestion } from "@/api/types";
import { SuggestionCard } from "@/components/analysis/SuggestionCard";
import { SetTrendChart } from "@/components/charts/SetTrendChart";
import { DiscussButton } from "@/components/chat/DiscussButton";
import { InsightCard } from "@/components/knowledge/InsightCard";
import { EmptyState } from "@/components/layout/EmptyState";
import { PageHeader } from "@/components/layout/PageHeader";
import { SectionCard } from "@/components/layout/SectionCard";
import { ContinueDesigning, DesigningBadge } from "@/components/sets/Designing";
import {
  type AutoFilled,
  fillFromProfile,
  ProfileTemperature,
  recipeHint,
  temperatureNote,
} from "@/components/sets/NewSetDialog";
import { ProposalCard } from "@/components/sets/ProposalCard";
import { RollbackButton } from "@/components/sets/RollbackButton";
import { SetSpread } from "@/components/sets/SetSpread";
import { VersionTimeline } from "@/components/sets/VersionTimeline";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useAnalyseSet, useSetSuggestions } from "@/hooks/useAnalysis";
import { useProfileVersions } from "@/hooks/useArchive";
import { useKnowledgeInsights } from "@/hooks/useKnowledge";
import { useQueryErrorToast } from "@/hooks/useQueryErrorToast";
import {
  useAddSetVersion,
  useArchiveSet,
  useDiscardDesign,
  useSet,
  useSetAutomatch,
  useSetTrends,
} from "@/hooks/useSets";
import { attempt } from "@/lib/mutations";
import { grindPatch, setSummary, trackRecordSentence, versionSummary } from "@/lib/sets";
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
  // Read from the refusal itself rather than copied into state: the next
  // press, the acknowledged one included, clears the error the moment it is
  // sent, and Cancel is `reset()`, so the strip cannot outlive the question it
  // asks or be answered twice. The page is not remounted when the route moves
  // to another Set, so the mutation (and its refusal) survives that; the
  // question belongs only to the Set it was asked about.
  const refused = analyse.variables;
  const largeBatch = refused?.setId === setId ? largeBatchCount(analyse.error) : null;
  const automatch = useSetAutomatch();
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
          <div className="flex flex-wrap items-center gap-2">
            {row.designing ? <DesigningBadge /> : null}
            {/* No automatch while designing: version 1 names no profile yet, so
                there is nothing to match a shot on, and a badge or a toggle
                would promise filing that cannot happen. */}
            {row.automatch && !row.designing ? (
              <Badge data-testid="set-automatch">automatch</Badge>
            ) : null}
            {row.archived ? <Badge variant="outline">archived</Badge> : null}
            {/* The conversation about the change being argued right now: the
                ledger, the spread and the evidence are in the prompt before the
                first word is typed, and a second press lands in the same room.
                While the Set is being designed that room is the design, and
                there is nothing yet to ask "how is it going" about. */}
            {row.designing ? (
              <ContinueDesigning set={row} />
            ) : (
              <DiscussButton
                setId={row.id}
                versionId={current?.id ?? null}
                question={`How is ${row.name} going, and what should I change next?`}
              />
            )}
            {row.archived || row.designing ? null : (
              <Button
                variant="outline"
                size="sm"
                disabled={automatch.isPending}
                onClick={() => automatch.mutate({ id: row.id, automatch: !row.automatch })}
              >
                <Coffee className="size-3.5" aria-hidden="true" />
                {row.automatch ? "Stop filing shots here" : "File matching shots here"}
              </Button>
            )}
            {row.archived ? null : (
              <Button
                variant="ghost"
                size="sm"
                disabled={archive.isPending}
                onClick={() => archive.mutate(row.id)}
              >
                <Archive className="size-3.5" aria-hidden="true" />
                Archive
              </Button>
            )}
          </div>
        }
      />

      {row.designing ? <DesignNotice set={row} /> : null}

      <SectionCard
        title={
          row.designing
            ? "No recipe yet: v1 is being designed"
            : `Now brewing: v${row.current_version_no}`
        }
        description={
          row.designing
            ? "The agent works it out with you in the conversation. You can also set the first recipe by hand: that fills version 1 and ends the design."
            : current
              ? versionSummary(current)
              : undefined
        }
        actions={
          <Button size="sm" variant="outline" onClick={() => setVersioning((open) => !open)}>
            <GitBranch className="size-3.5" aria-hidden="true" />
            {row.designing ? "Set the first recipe by hand" : "Change something"}
          </Button>
        }
      >
        {versioning ? (
          <NewVersionForm
            setId={row.id}
            versions={detail.data.versions}
            designing={row.designing}
            onDone={() => setVersioning(false)}
          />
        ) : row.designing ? (
          <p className="text-muted-foreground text-sm" data-testid="design-by-hand">
            A card the agent proposed is answered where it is shown, in the conversation or in the
            log below. Recording a recipe by hand instead sets any waiting card aside.
          </p>
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
            {row.designing
              ? "No shots yet. Once the first recipe is accepted and its profile pushed, shots brewed on it are filed here."
              : "No shots yet. The next one pulled with this Set's profile files itself here."}
          </p>
        ) : (
          <SetTrendChart trends={trends.data as NonNullable<typeof trends.data>} />
        )}
      </SectionCard>

      <SetInsights setId={row.id} />

      <SectionCard
        title="The experiment log"
        description="Newest first: what changed, what you were trying, what you predicted it would do, the shots it produced and how the prediction turned out."
      >
        <TrackRecord detail={detail.data} setId={row.id} />
        {/* Above everything in the log, because it is the only thing here that
            is a question rather than a record: a change somebody is waiting to
            answer, and the log's first entry is not it yet. */}
        {detail.data.proposal ? (
          <div className="mb-3">
            <ProposalCard setId={row.id} proposal={detail.data.proposal} showThreadLink />
          </div>
        ) : null}
        {/* Above the log, because it is what every comparison in the log is
            held against: three seconds is a lot or nothing depending on it. */}
        <SetSpread spread={detail.data.spread ?? []} />
        <VersionTimeline
          setId={row.id}
          versions={detail.data.versions}
          judgements={detail.data.judgements}
          designing={row.designing}
        />
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
        {largeBatch !== null ? (
          <LargeBatchWarning
            count={largeBatch}
            // The refused request itself, acknowledged: nothing else about it
            // may change between the question and the answer.
            onConfirm={() => refused && analyse.mutate({ ...refused, acknowledgeLargeBatch: true })}
            onCancel={() => analyse.reset()}
          />
        ) : null}
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
 * The question a Set batch over the server's limit asks before it spends.
 *
 * The count is the server's (what it would actually queue, running shots left
 * out), never one worked out here. The time is the batch's own arithmetic: two
 * calls at a time, about a minute each.
 */
function LargeBatchWarning({
  count,
  onConfirm,
  onCancel,
}: {
  count: number;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const minutes = Math.max(1, Math.round(count / 2));
  return (
    <div
      // A polite live region, not an alert: this is a question about to be
      // answered, not a fault that should interrupt what is being read.
      role="status"
      className="mb-3 space-y-2 rounded-md border border-amber-500/50 bg-amber-500/10 p-3"
      data-testid="large-batch-warning"
    >
      <p className="text-sm">
        This would run <strong>{count} analyses</strong>, one provider call per shot, for about{" "}
        {minutes} minute{minutes === 1 ? "" : "s"}.
      </p>
      <div className="flex flex-wrap gap-2">
        <Button size="sm" data-testid="large-batch-confirm" onClick={onConfirm}>
          Analyse all {count}
        </Button>
        <Button size="sm" variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </div>
  );
}

/** What a refused discard means, in words the person can act on. */
function discardRefusal(error: Error | null): string | null {
  if (!(error instanceof ApiClientError)) return null;
  if (error.code === "DESIGN_HAS_SHOTS") {
    return "A shot is filed on this Set, so it is kept. Move the shot to another Set first, or archive this one.";
  }
  if (error.code === "NOT_DESIGNING") {
    return "This Set has a recipe now, so it is no longer a design to discard. Archive it instead.";
  }
  return null;
}

/**
 * What the design was asked for: the profile to fork, the usual grind, the goal.
 *
 * The same brief the agent is given on every turn, so the person can see what
 * the conversation is working from without scrolling back to its first
 * message. Only the parts that were given; nothing at all for an empty brief.
 * The fork's label comes from the profile versions the page already reads for
 * the version form (one cached query), with the id as the fallback while that
 * list is loading or when the version is older than it reaches.
 */
function DesignBrief({ brief }: { brief: SetRow["design_brief"] }) {
  const profiles = useProfileVersions({ limit: 200 });
  const forkId = brief?.fork_profile_version_id ?? null;
  const usualGrind = brief?.usual_grind?.trim() ?? "";
  const goal = brief?.goal?.trim() ?? "";
  if (forkId === null && !usualGrind && !goal) return null;
  const forkLabel =
    forkId === null
      ? null
      : ((profiles.data?.items ?? []).find((item) => item.id === forkId)?.label ??
        `profile version #${forkId}`);
  return (
    <dl className="mb-2 space-y-1 text-sm" data-testid="design-brief">
      {forkLabel ? (
        <div className="flex gap-1.5">
          <dt className="shrink-0 text-muted-foreground">Forking</dt>
          <dd className="min-w-0" data-testid="design-brief-fork">
            {forkLabel}
          </dd>
        </div>
      ) : null}
      {usualGrind ? (
        <div className="flex gap-1.5">
          <dt className="shrink-0 text-muted-foreground">Usual grind</dt>
          <dd className="min-w-0 tabular-nums" data-testid="design-brief-grind">
            {usualGrind}
          </dd>
        </div>
      ) : null}
      {goal ? (
        <div className="flex gap-1.5">
          <dt className="shrink-0 text-muted-foreground">Asked for</dt>
          {/* Up to 2000 characters were allowed, and this is a notice, not
              the conversation: three lines, the whole text on hover. */}
          <dd
            className="line-clamp-3 min-w-0 break-words"
            title={goal}
            data-testid="design-brief-goal"
          >
            “{goal}”
          </dd>
        </div>
      ) : null}
    </dl>
  );
}

/**
 * A Set whose recipe is still being worked out, and the way to give up on it.
 *
 * Discarding deletes the Set and its conversations, which is why it asks first
 * — inline, in a strip rendered always and toggled with `hidden`, rather than
 * in an overlay a test would have to open. The server allows it only while
 * nothing is filed on the Set and it has no recipe; a design somebody brewed
 * under has history and is archived instead, and the two refusals say so here.
 */
function DesignNotice({ set }: { set: SetRow }) {
  const setId = set.id;
  const discard = useDiscardDesign();
  const navigate = useNavigate();
  const [confirming, setConfirming] = useState(false);
  const regionId = useId();
  const refusal = discard.isPending ? null : discardRefusal(discard.error);

  return (
    <SectionCard
      title="Being designed"
      description="This Set has a bean and a grinder and no recipe yet. The agent is working out version 1 with you in its conversation; nothing is brewed or filed here until a recipe is accepted."
      actions={
        <Button
          size="sm"
          variant="ghost"
          aria-expanded={confirming}
          aria-controls={regionId}
          data-testid="discard-design"
          onClick={() => setConfirming((open) => !open)}
        >
          <Trash2 className="size-3.5" aria-hidden="true" />
          Discard design
        </Button>
      }
    >
      <DesignBrief brief={set.design_brief} />
      <div id={regionId} hidden={!confirming} className="space-y-2" data-testid="discard-confirm">
        <p className="text-sm">
          Discard this Set? It is deleted with its conversations, and a profile draft a waiting card
          carried is discarded. Nothing was sent to the machine.
        </p>
        <div className="flex flex-wrap gap-2">
          <Button
            size="sm"
            variant="destructive"
            disabled={discard.isPending}
            onClick={async () => {
              const done = await attempt(() => discard.mutateAsync(setId));
              if (done) navigate("/sets");
            }}
          >
            {discard.isPending ? "Discarding…" : "Discard it"}
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setConfirming(false)}>
            Keep designing
          </Button>
        </div>
      </div>
      {refusal ? (
        <p className="mt-2 text-destructive text-sm" data-testid="discard-refused">
          {refusal}
        </p>
      ) : null}
    </SectionCard>
  );
}

/**
 * How often the predictions held, and the way back when they have stopped.
 *
 * The sentence is nothing until something has been graded — zero out of zero is
 * an absence, not a modest score. The roll-back offer appears only when the
 * archive can see that the current version is going badly: it has a shot you
 * said to improve on and none you said to keep, and there is an earlier version
 * you did keep shots from. Anything less than that and the button would be
 * nagging.
 */
function TrackRecord({ detail, setId }: { detail: SetDetailData; setId: number }) {
  const sentence = trackRecordSentence(detail.track_record);
  const record = detail.track_record;
  const current = detail.versions[0];
  const target = detail.versions.find(
    (entry) => entry.version.id === detail.rollback_target_version_id,
  );
  const struggling =
    current !== undefined && current.labels.improve > 0 && current.labels.keep === 0;

  if (!sentence && !(target && struggling)) return null;

  return (
    <div className="mb-3 space-y-2" data-testid="track-record">
      {sentence ? (
        <p className="text-sm">
          <span className="font-medium">{sentence}</span>
          <span className="text-muted-foreground">
            {` · ${record.open} open · ${record.no_prediction} with no prediction`}
          </span>
        </p>
      ) : null}
      {target && struggling ? (
        <RollbackButton
          setId={setId}
          versionId={target.version.id}
          versionNo={target.version.version_no}
          label={`Roll back to v${target.version.version_no}, the last version with Keep shots`}
          icon={<Undo2 className="size-3.5" aria-hidden="true" />}
        />
      ) : null}
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
 * would record all five as changed and the timeline's diff would say nothing.
 */
function NewVersionForm({
  setId,
  versions,
  designing = false,
  onDone,
}: {
  setId: number;
  versions: SetDetailData["versions"];
  /**
   * The Set is still being designed: what is recorded here fills version 1 in
   * place and ends the design. There is nothing for a prediction to be
   * compared to, and the server ignores one there, so none is asked for.
   */
  designing?: boolean;
  onDone: () => void;
}) {
  const add = useAddSetVersion();
  const profiles = useProfileVersions({ limit: 200 });
  const [grind, setGrind] = useState("");
  const [dose, setDose] = useState("");
  const [target, setTarget] = useState("");
  const [intent, setIntent] = useState("");
  const [prediction, setPrediction] = useState("");
  // The current version, which is what "compared to" means unless somebody says
  // otherwise: this version is a change to that one.
  const current = versions[0]?.version;
  const [compare, setCompare] = useState(current ? String(current.id) : "");
  // The profile is the one recipe field that starts *filled*, because leaving a
  // select blank is not how anybody says "keep the profile I have" — the other
  // four are numbers, where blank reads as "unchanged" on its own.
  const inheritedProfile = current?.profile_version_id ? String(current.profile_version_id) : "";
  const [profile, setProfile] = useState(inheritedProfile);
  const [filled, setFilled] = useState<AutoFilled>({ targetYieldG: null });
  const ids = {
    profile: useId(),
    grind: useId(),
    dose: useId(),
    target: useId(),
    intent: useId(),
    prediction: useId(),
    compare: useId(),
  };

  function number(value: string): number | undefined {
    const parsed = Number.parseFloat(value);
    return Number.isFinite(parsed) && parsed > 0 ? parsed : undefined;
  }

  /** The same rule the New Set form uses, through the same helper. */
  function pickProfile(versionId: string) {
    const version = (profiles.data?.items ?? []).find((row) => String(row.id) === versionId);
    const result = fillFromProfile({ targetYieldG: target }, filled, version);
    setTarget(result.values.targetYieldG);
    setFilled(result.filled);
    setProfile(versionId);
  }

  const fromProfile = recipeHint({ targetYieldG: target }, filled);
  const chosenProfile = (profiles.data?.items ?? []).find((row) => String(row.id) === profile);

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
              prediction: designing ? "" : prediction.trim(),
              // Sent explicitly whenever there is a prediction, including as
              // null: omitting the key means "against the parent", and an empty
              // select means "against nothing". With no prediction there is
              // nothing to compare, so the key is left off entirely.
              ...(!designing && prediction.trim()
                ? { compares_to_version_id: compare ? Number(compare) : null }
                : {}),
              origin: "manual",
              // Untouched means inherited, like every other recipe field here;
              // changed means sent, including to null when somebody picks
              // "Any profile" on a version that named one.
              ...(profile !== inheritedProfile
                ? { profile_version_id: profile ? Number(profile) : null }
                : {}),
              ...grindPatch(grind),
              ...(number(dose) ? { dose_g: number(dose) } : {}),
              ...(number(target) ? { target_yield_g: number(target) } : {}),
            },
          }),
        );
        if (version) onDone();
      }}
    >
      {designing ? (
        <p className="font-medium text-sm" data-testid="new-version-designing">
          Set the first recipe by hand. It fills version 1 and ends the design; a card waiting in
          the conversation is set aside.
        </p>
      ) : (
        <p className="text-muted-foreground text-xs">
          Fill in only what changed. Anything left blank carries over from the current version.
        </p>
      )}
      {/* First, because it is the change that goes wrong most quietly: somebody
          who switched profiles on the machine and did not record it here finds
          their next shots in "needs a Set", since auto-assignment matches on
          the profile. */}
      <Labelled id={ids.profile} label="Profile">
        <select
          id={ids.profile}
          className={FIELD}
          value={profile}
          onChange={(event) => pickProfile(event.target.value)}
        >
          <option value="">Any profile</option>
          {(profiles.data?.items ?? []).map((version) => (
            <option key={version.id} value={String(version.id)}>
              {version.label}
            </option>
          ))}
        </select>
      </Labelled>
      <p className="text-muted-foreground text-xs" data-testid="profile-help">
        Recording a profile here changes nothing on the machine — it says which profile this version
        was brewed with, so shots pulled with it join the Set on their own. Putting a profile on the
        machine is done from the Profiles page, by you.
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
        <ProfileTemperature version={chosenProfile} />
      </div>
      <p className="text-muted-foreground text-xs" data-testid="temperature-from-profile">
        {temperatureNote(chosenProfile)}
      </p>
      {fromProfile ? (
        <p className="text-muted-foreground text-xs" data-testid="version-from-profile">
          {fromProfile} Change it and it stays yours.
        </p>
      ) : null}
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
      {/* Optional, and deliberately separate from the intent: the intent is
          what you are attempting, the prediction is what you are claiming will
          happen. Only the second one can turn out to be wrong. */}
      {designing ? null : (
        <div className="grid gap-3 sm:grid-cols-[2fr_1fr]">
          <Labelled id={ids.prediction} label="Version prediction">
            <textarea
              id={ids.prediction}
              rows={2}
              maxLength={1000}
              className={cn(FIELD, "h-auto py-1.5")}
              placeholder="Compared to v4: less bitter, a shorter shot"
              value={prediction}
              onChange={(event) => setPrediction(event.target.value)}
            />
          </Labelled>
          <Labelled id={ids.compare} label="Compared to">
            <select
              id={ids.compare}
              className={FIELD}
              value={compare}
              onChange={(event) => setCompare(event.target.value)}
            >
              <option value="">Nothing — grade it on its own numbers</option>
              {versions.map((entry) => (
                <option key={entry.version.id} value={String(entry.version.id)}>
                  v{entry.version.version_no}
                </option>
              ))}
            </select>
          </Labelled>
        </div>
      )}
      <div className="flex gap-2">
        <Button type="submit" size="sm" disabled={add.isPending}>
          {add.isPending ? "Recording…" : designing ? "Set version 1" : "Record the version"}
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
