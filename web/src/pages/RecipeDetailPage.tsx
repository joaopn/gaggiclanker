import { AlertTriangle, Archive, ArrowLeft, Coffee, GitBranch, Trash2, Undo2 } from "lucide-react";
import { useId, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ApiClientError } from "@/api/client";
import type { Drink, RecipeDetailData, RecipeRow } from "@/api/types";
import { RecipeTrendChart } from "@/components/charts/RecipeTrendChart";
import { DiscussButton } from "@/components/chat/DiscussButton";
import { EmptyState } from "@/components/layout/EmptyState";
import { PageHeader } from "@/components/layout/PageHeader";
import { SectionCard } from "@/components/layout/SectionCard";
import { ContinueDesigning, DesigningBadge } from "@/components/recipes/Designing";
import { DrinkBadge } from "@/components/recipes/DrinkBadge";
import { DrinkFields } from "@/components/recipes/DrinkFields";
import { DuplicateDialog } from "@/components/recipes/DuplicateDialog";
import { duplicateButtonId } from "@/components/recipes/duplicateIds";
import { MajorChoice } from "@/components/recipes/MajorChoice";
import { PinnedCard } from "@/components/recipes/PinnedCard";
import { ProposalCard } from "@/components/recipes/ProposalCard";
import { RecipeInsights } from "@/components/recipes/RecipeInsights";
import { RecipeSignatureLine } from "@/components/recipes/RecipeSignatureLine";
import { RecipeSpread } from "@/components/recipes/RecipeSpread";
import { RollbackButton } from "@/components/recipes/RollbackButton";
import { VersionTimeline } from "@/components/recipes/VersionTimeline";
import { ConfirmStrip } from "@/components/sync/ConfirmStrip";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useProfileVersions } from "@/hooks/useArchive";
import { useDuplicateDialog } from "@/hooks/useDuplicateDialog";
import { useQueryErrorToast } from "@/hooks/useQueryErrorToast";
import {
  useAddRecipeVersion,
  useArchiveRecipe,
  useDiscardDesign,
  useProfileChoices,
  useRecipe,
  useRecipeTrends,
  useSetAutomatch,
} from "@/hooks/useRecipes";
import { useSingleFlight } from "@/hooks/useSingleFlight";
import { attempt } from "@/lib/mutations";
import {
  archiveWords,
  currentVersion,
  grindPatch,
  recipeSummary,
  trackRecordSentence,
  versionSummary,
} from "@/lib/recipes";
import { formatDate } from "@/lib/shots";
import { cn } from "@/lib/utils";

/**
 * One Recipe: what it is, how it has gone, and every recipe it has been through.
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

export function RecipeDetailPage() {
  const params = useParams();
  const recipeId = Number.parseInt(params.recipeId ?? "", 10);
  // A route param that is not a number is a typed-in URL, and the query is
  // never enabled for it — so it has to be treated as "no such Recipe" here rather
  // than left as a pending query, which renders a skeleton that never resolves.
  const valid = Number.isFinite(recipeId);
  const detail = useRecipe(valid ? recipeId : undefined);
  const trends = useRecipeTrends(valid ? recipeId : undefined);
  const automatch = useSetAutomatch();
  const archive = useArchiveRecipe();
  const [versioning, setVersioning] = useState(false);
  // Which Recipe the question is about: the page keeps its state across `/recipes/:recipeId`, so a bare flag
  // would carry an open question over to the next Recipe.
  const [archivingId, setArchivingId] = useState<number | null>(null);
  // Cleared when the route moves: coming back to a Recipe must not find the question still open.
  const [archivingSeen, setArchivingSeen] = useState(recipeId);
  if (!Object.is(archivingSeen, recipeId)) {
    setArchivingSeen(recipeId);
    setArchivingId(null);
  }
  const archiveOnce = useSingleFlight();
  useQueryErrorToast(detail.error, "Could not load this Recipe");
  const duplicate = useDuplicateDialog(recipeId, detail.data?.versions);

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
          title="No such Recipe"
          description={
            valid
              ? (detail.error?.message ?? "The archive has no Recipe with that id.")
              : "That is not a Recipe id."
          }
        />
      </div>
    );
  }

  const row = detail.data.recipe;
  const current = currentVersion(detail.data.versions)?.version;
  // A Recipe still being designed serves no drink until version 1 is filled from the brief; the
  // brief already holds the one the person chose, so the heading shows that meanwhile.
  const brief = row.designing ? row.design_brief : null;
  const headingDrink = row.drink ?? brief?.drink ?? null;
  const headingNotes = row.drink_notes || brief?.drink_notes || "";
  const duplicating =
    duplicate.versionId === null
      ? undefined
      : detail.data.versions.find((entry) => entry.version.id === duplicate.versionId);

  return (
    <div className="space-y-4">
      <BackLink />
      <PageHeader
        title={row.name}
        titleBadge={<DrinkBadge drink={headingDrink} />}
        subtitle={recipeSummary(row)}
        detail={
          headingNotes ? (
            <p className="break-words" data-testid="recipe-drink-notes">
              <span className="text-muted-foreground">Drink notes: </span>
              {headingNotes}
            </p>
          ) : null
        }
        actions={
          <div className="flex flex-wrap items-center gap-2">
            {row.designing ? <DesigningBadge /> : null}
            {/* No automatch while designing: version 1 names no profile yet, so
                there is nothing to match a shot on, and a badge or a toggle
                would promise filing that cannot happen. */}
            {row.automatch && !row.designing ? (
              <Badge data-testid="recipe-automatch">automatch</Badge>
            ) : null}
            {row.archived ? <Badge variant="outline">archived</Badge> : null}
            {/* The conversation about the change being argued right now: the
                ledger, the spread and the evidence are in the prompt before the
                first word is typed, and a second press lands in the same room.
                While the Recipe is being designed that room is the design, and
                there is nothing yet to ask "how is it going" about. */}
            {row.designing ? (
              <ContinueDesigning recipe={row} />
            ) : (
              <DiscussButton
                recipeId={row.id}
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
                disabled={archive.isPending || archivingId === row.id}
                onClick={() =>
                  // A Recipe with pins asks first: archiving takes them off the machine.
                  detail.data.pins.length > 0
                    ? setArchivingId(row.id)
                    : archiveOnce((release) => archive.mutate(row.id, { onSettled: release }))
                }
              >
                <Archive className="size-3.5" aria-hidden="true" />
                Archive
              </Button>
            )}
          </div>
        }
      />

      {row.copied_from ? (
        <p className="break-words text-muted-foreground text-sm" data-testid="copied-from">
          Copied from{" "}
          <Link
            to={`/recipes/${row.copied_from.recipe_id}`}
            className="underline underline-offset-2"
            data-testid="copied-from-link"
          >
            {row.copied_from.recipe_name} {row.copied_from.version_label} ↗
          </Link>{" "}
          on {formatDate(row.created_at)}
        </p>
      ) : null}

      {archivingId === row.id && detail.data.pins.length > 0 ? (
        <ConfirmStrip
          title={`Archive ${row.name}?`}
          confirmLabel="Archive"
          confirmVariant="default"
          testId="archive-confirm"
          focusOnOpen
          onCancel={() => setArchivingId(null)}
          onConfirm={() =>
            archiveOnce((release) =>
              archive.mutate(row.id, {
                onSuccess: () => setArchivingId(null),
                onSettled: release,
              }),
            )
          }
        >
          <p data-testid="archive-pins">{archiveWords(detail.data.pins)}</p>
        </ConfirmStrip>
      ) : null}

      {row.designing ? <DesignNotice recipe={row} /> : null}

      <SectionCard
        title={
          row.designing
            ? "No numbers yet: v1 is being designed"
            : `Now brewing: ${row.current_version_label}`
        }
        description={
          row.designing
            ? "The agent works it out with you in the conversation. You can also write version 1 by hand: that fills it and ends the design."
            : current
              ? versionSummary(current)
              : undefined
        }
        actions={
          <Button size="sm" variant="outline" onClick={() => setVersioning((open) => !open)}>
            <GitBranch className="size-3.5" aria-hidden="true" />
            {row.designing ? "Write version 1 by hand" : "Change something"}
          </Button>
        }
      >
        {versioning ? (
          <NewVersionForm
            // Remounted when the Recipe goes back (or on) to another version: the
            // untouched profile and "compared to" are inherited from the current
            // version, and a form opened before the move would hold the old one.
            key={current?.id}
            recipeId={row.id}
            versions={detail.data.versions}
            designing={row.designing}
            needsProfile={row.board_id == null}
            nextMinorLabel={row.next_minor_label}
            nextMajorLabel={row.next_major_label}
            onDone={() => setVersioning(false)}
          />
        ) : row.designing ? (
          <p className="text-muted-foreground text-sm" data-testid="design-by-hand">
            A card the agent proposed is answered where it is shown, in the conversation or in the
            log below. Writing version 1 by hand instead sets any waiting card aside.
          </p>
        ) : (
          <p className="text-muted-foreground text-sm">
            {row.shot_count} shot{row.shot_count === 1 ? "" : "s"} across {row.version_count}{" "}
            version{row.version_count === 1 ? "" : "s"}. Changing the grind, the dose, the profile,
            the drink or its notes records a new version, so the archive can tell you what the
            change did.
          </p>
        )}
        {row.designing ? null : (
          <RecipeSignatureLine
            profileVersionId={current?.profile_version_id ?? null}
            recipeId={row.id}
            recipeVersionId={current?.id ?? null}
          />
        )}
      </SectionCard>

      <PinnedCard pins={detail.data.pins} onDuplicate={duplicate.open} />

      <SectionCard
        title="How it has gone"
        description="One point per shot, oldest first, with a dashed line wherever the version changed: your rating on the left, the shot time and the ratio beside it."
      >
        {trends.isPending ? (
          <Skeleton className="h-56 w-full" />
        ) : (trends.data?.shots.length ?? 0) === 0 ? (
          <p className="text-muted-foreground text-sm">
            {row.designing
              ? "No shots yet. Once version 1 is accepted and its profile on the machine, shots brewed on it are filed here."
              : "No shots yet. The next one pulled with this Recipe's profile files itself here."}
          </p>
        ) : (
          <RecipeTrendChart trends={trends.data as NonNullable<typeof trends.data>} />
        )}
      </SectionCard>

      <RecipeInsights
        recipeId={row.id}
        versionLabels={detail.data.versions.map((entry) => entry.version.version_label)}
      />

      <SectionCard
        title="The experiment log"
        description="Newest first: what changed, what you were trying, what you predicted it would do, the shots it produced and how the prediction turned out."
      >
        <TrackRecord detail={detail.data} recipeId={row.id} />
        {/* Above everything in the log, because it is the only thing here that
            is a question rather than a record: a change somebody is waiting to
            answer, and the log's first entry is not it yet. */}
        {detail.data.proposal ? (
          <div className="mb-3">
            <ProposalCard recipeId={row.id} proposal={detail.data.proposal} showThreadLink />
          </div>
        ) : null}
        {/* Above the log, because it is what every comparison in the log is
            held against: three seconds is a lot or nothing depending on it. */}
        <RecipeSpread spread={detail.data.spread ?? []} />
        <VersionTimeline
          recipeId={row.id}
          versions={detail.data.versions}
          judgements={detail.data.judgements}
          reverts={detail.data.reverts}
          designing={row.designing}
          archived={row.archived}
          onDuplicate={(versionId) => duplicate.open(versionId, duplicateButtonId(versionId))}
        />
      </SectionCard>

      {duplicating ? (
        <DuplicateDialog
          open
          recipeId={row.id}
          recipeName={row.name}
          version={duplicating.version}
          openerId={duplicate.openerId}
          onOpenChange={(next) => {
            if (!next) duplicate.close();
          }}
        />
      ) : null}
    </div>
  );
}

/** What a refused discard means, in words the person can act on. */
function discardRefusal(error: Error | null): string | null {
  if (!(error instanceof ApiClientError)) return null;
  if (error.code === "DESIGN_HAS_SHOTS") {
    return "A shot is filed on this Recipe, so it is kept. Move the shot to another Recipe first, or archive this one.";
  }
  if (error.code === "NOT_DESIGNING") {
    return "This Recipe has a first version now, so it is no longer a design to discard. Archive it instead.";
  }
  return null;
}

/**
 * What the design was asked for: the profile to fork, the usual grind, the goal,
 * and the grind, dose and yield the person filled in for the agent to keep.
 *
 * The same brief the agent is given on every turn, so the person can see what
 * the conversation is working from without scrolling back to its first
 * message. Only the parts that were given; nothing at all for an empty brief.
 * The fork's label comes from the newest profile versions (one cached query), with the id as
 * the fallback while that list is loading or when the version is older than it reaches.
 */
function DesignBrief({ brief }: { brief: RecipeRow["design_brief"] }) {
  const profiles = useProfileVersions({ limit: 200 });
  const forkId = brief?.fork_profile_version_id ?? null;
  const usualGrind = brief?.usual_grind?.trim() ?? "";
  const goal = brief?.goal?.trim() ?? "";
  const kept = [
    brief?.grind_setting?.trim() ? `grind ${brief.grind_setting.trim()}` : "",
    brief?.dose_g != null ? `${brief.dose_g} g in` : "",
    brief?.target_yield_g != null ? `${brief.target_yield_g} g out` : "",
  ]
    .filter(Boolean)
    .join(" · ");
  if (forkId === null && !usualGrind && !goal && !kept) return null;
  const forkLabel =
    forkId === null
      ? null
      : ((profiles.data?.items ?? []).find((item) => item.id === forkId)?.profile_name ??
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
      {kept ? (
        <div className="flex gap-1.5">
          <dt className="shrink-0 text-muted-foreground">Keeping</dt>
          <dd className="min-w-0 tabular-nums" data-testid="design-brief-kept">
            {kept}
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
 * A Recipe whose recipe is still being worked out, and the way to give up on it.
 *
 * Discarding deletes the Recipe and its conversations, which is why it asks first
 * — inline, in a strip rendered always and toggled with `hidden`, rather than
 * in an overlay a test would have to open. The server allows it only while
 * nothing is filed on the Recipe and it has no recipe; a design somebody brewed
 * under has history and is archived instead, and the two refusals say so here.
 */
function DesignNotice({ recipe }: { recipe: RecipeRow }) {
  const recipeId = recipe.id;
  const discard = useDiscardDesign();
  const navigate = useNavigate();
  const [confirming, setConfirming] = useState(false);
  const regionId = useId();
  const refusal = discard.isPending ? null : discardRefusal(discard.error);

  return (
    <SectionCard
      title="Being designed"
      description="This Recipe has a bean and a grinder and no version yet. The agent is working out version 1 with you in its conversation; nothing is brewed or filed here until version 1 is accepted."
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
      <DesignBrief brief={recipe.design_brief} />
      <div id={regionId} hidden={!confirming} className="space-y-2" data-testid="discard-confirm">
        <p className="text-sm">
          Discard this Recipe? It is deleted with its conversations, and a profile draft a waiting
          card carried is discarded. Nothing was sent to the machine.
        </p>
        <div className="flex flex-wrap gap-2">
          <Button
            size="sm"
            variant="destructive"
            disabled={discard.isPending}
            onClick={async () => {
              const done = await attempt(() => discard.mutateAsync(recipeId));
              if (done) navigate("/recipes");
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
 * archive can see that the current version is going badly: it has a Bad shot
 * and no Good one, and there is an earlier version that had Good shots. Anything less than that and the button would be
 * nagging.
 */
function TrackRecord({ detail, recipeId }: { detail: RecipeDetailData; recipeId: number }) {
  const sentence = trackRecordSentence(detail.track_record);
  const record = detail.track_record;
  const current = currentVersion(detail.versions);
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
          recipeId={recipeId}
          versionId={target.version.id}
          versionLabel={target.version.version_label}
          label={`Go back to ${target.version.version_label}, the last version with Good shots`}
          icon={<Undo2 className="size-3.5" aria-hidden="true" />}
        />
      ) : null}
    </div>
  );
}

function BackLink() {
  return (
    <Link
      to="/recipes"
      className="inline-flex items-center gap-1 text-muted-foreground text-sm hover:text-foreground"
    >
      <ArrowLeft className="size-3.5" aria-hidden="true" />
      All Recipes
    </Link>
  );
}

/**
 * A new version: only the fields you changed, plus what you are trying.
 *
 * The numbers start empty rather than prefilled with the current version, and
 * that is the whole design of the endpoint showing through: an omitted field
 * inherits the parent's value, so a form prefilled with the current values
 * would record every number as changed and the timeline's diff would say nothing.
 *
 * **The drink and its notes are the exception: they start filled** with the current version's,
 * because "empty keeps the value" cannot say "remove the notes". What is sent is still only what
 * differs from the current version, so an untouched field changes nothing. Another drink
 * switches the major box on and says why; the person can switch it back. Notes alone suggest
 * nothing, and clearing them sends an empty string. A version that states no drink offers "Not
 * stated", and the drink is then sent only if one is picked (never as null: the server refuses
 * a drink once stated being unstated).
 */
function NewVersionForm({
  recipeId,
  versions,
  designing = false,
  needsProfile = false,
  nextMinorLabel,
  nextMajorLabel,
  onDone,
}: {
  recipeId: number;
  versions: RecipeDetailData["versions"];
  /** What the version is called as a minor and as a major, from the server. */
  nextMinorLabel: string;
  nextMajorLabel: string;
  /**
   * The Recipe is still being designed: what is recorded here fills version 1 in
   * place and ends the design. There is nothing for a prediction to be
   * compared to, and the server ignores one there, so none is asked for.
   */
  designing?: boolean;
  /**
   * The Recipe has no profile (one being designed, or an old one that never named one): this
   * version gives it the active version of a profile no Recipe has, as New Recipe does. A Recipe that
   * has a profile has no choice here: its versions follow the profile.
   */
  needsProfile?: boolean;
  onDone: () => void;
}) {
  const add = useAddRecipeVersion();
  const choices = useProfileChoices({ enabled: needsProfile });
  const [profile, setProfile] = useState("");
  const [grind, setGrind] = useState("");
  const [dose, setDose] = useState("");
  const [target, setTarget] = useState("");
  const [intent, setIntent] = useState("");
  const [prediction, setPrediction] = useState("");
  // The current version, which is what "compared to" means unless somebody says
  // otherwise: this version is a change to that one.
  const current = currentVersion(versions)?.version;
  // Filled with the current version's, unlike the numbers: see above. "" is a drink not stated.
  const currentDrink = current?.drink ?? "";
  const currentNotes = current?.drink_notes ?? "";
  const [drink, setDrink] = useState(currentDrink);
  const [drinkNotes, setDrinkNotes] = useState(currentNotes);
  const [compare, setCompare] = useState(current ? String(current.id) : "");
  // "Major change": the person's answer once they have given one; until then the box is
  // unticked, because grind, dose and yield are dialling in: minor versions.
  const [majorChoice, setMajorChoice] = useState<boolean | null>(null);
  const ids = {
    grind: useId(),
    dose: useId(),
    target: useId(),
    intent: useId(),
    prediction: useId(),
    compare: useId(),
    profile: useId(),
  };

  function number(value: string): number | undefined {
    const parsed = Number.parseFloat(value);
    return Number.isFinite(parsed) && parsed > 0 ? parsed : undefined;
  }

  // Another drink is a major change; nothing is suggested for the notes alone.
  const drinkChanged = drink !== "" && drink !== currentDrink;
  const major = majorChoice ?? false;
  // Only a changed value is sent. Whitespace around notes is the server's to strip, so it is not
  // a change either.
  const notesChanged = drinkNotes.trim() !== currentNotes.trim();

  return (
    <form
      data-testid="new-version-form"
      className="space-y-3"
      onSubmit={async (event) => {
        event.preventDefault();
        const version = await attempt(() =>
          add.mutateAsync({
            recipeId,
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
              ...(needsProfile && profile ? { profile_version_id: Number(profile) } : {}),
              ...grindPatch(grind),
              ...(number(dose) ? { dose_g: number(dose) } : {}),
              ...(number(target) ? { target_yield_g: number(target) } : {}),
              // Nothing while designing: version 1 takes the drink from the brief whatever is sent.
              ...(!designing && drinkChanged ? { drink: drink as Drink } : {}),
              ...(!designing && notesChanged ? { drink_notes: drinkNotes.trim() } : {}),
              // What the box shows is what is sent. Nothing while designing:
              // the recipe fills version 1 either way.
              ...(designing ? {} : { major }),
            },
          }),
        );
        if (version) onDone();
      }}
    >
      {designing ? (
        <p className="font-medium text-sm" data-testid="new-version-designing">
          Write version 1 by hand. It fills it and ends the design; a card waiting in the
          conversation is set aside.
        </p>
      ) : (
        <p className="text-muted-foreground text-xs">
          Fill in only what changed. Anything left blank carries over from the current version.
        </p>
      )}
      {needsProfile ? (
        <Labelled id={ids.profile} label="Profile">
          <select
            id={ids.profile}
            required
            className={FIELD}
            value={profile}
            onChange={(event) => setProfile(event.target.value)}
          >
            <option value="">Pick a profile</option>
            {(choices.data?.items ?? []).map((choice) => (
              <option key={choice.profile_version_id} value={String(choice.profile_version_id)}>
                {choice.label}
              </option>
            ))}
          </select>
        </Labelled>
      ) : null}
      <div className="grid gap-3 sm:grid-cols-3">
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
      </div>
      {/* While designing, version 1 takes the drink from the brief the Recipe was started with,
          so a field here would promise what the server then ignores. */}
      {designing ? null : (
        <DrinkFields
          drink={drink}
          onDrinkChange={(value) => {
            setDrink(value);
            // Another drink is a major change, and putting the old one back puts the choice back
            // to unsaid; either way the box stays the person's.
            setMajorChoice(value !== "" && value !== currentDrink ? true : null);
          }}
          notes={drinkNotes}
          onNotesChange={setDrinkNotes}
          unstated={currentDrink === ""}
          drinkHint="Shows the current drink; pick another to change it."
          notesHint="Shows the current notes; edit them, or clear to remove."
          disabled={add.isPending}
        />
      )}
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
                  {entry.version.version_label}
                </option>
              ))}
            </select>
          </Labelled>
        </div>
      )}
      {designing ? null : (
        <MajorChoice
          checked={major}
          onChange={setMajorChoice}
          minorLabel={nextMinorLabel}
          majorLabel={nextMajorLabel}
          suggested={drinkChanged ? "the drink changes" : ""}
          disabled={add.isPending}
        />
      )}
      <div className="flex gap-2">
        <Button type="submit" size="sm" disabled={add.isPending}>
          {add.isPending
            ? "Recording…"
            : designing
              ? "Recipe version 1"
              : `Record it as ${major ? nextMajorLabel : nextMinorLabel}`}
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
