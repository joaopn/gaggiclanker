import { ArrowRight, Undo2 } from "lucide-react";
import { useId, useState } from "react";
import { Link } from "react-router-dom";
import type { SetRevertRow, SetVersionDetail, ShotJudgement } from "@/api/types";
import { VersionEvidence } from "@/components/sets/VersionEvidence";
import { VersionOutcomeControl } from "@/components/sets/VersionOutcomeControl";
import { VersionOverrides } from "@/components/sets/VersionOverrides";
import { VersionPredictionEditor } from "@/components/sets/VersionPredictionEditor";
import { CheckBadge } from "@/components/shots/CheckBadge";
import { RatingStars } from "@/components/shots/RatingStars";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useVocabulary } from "@/hooks/useCatalog";
import { currentVersion, labelSummary, logItems, versionRatio, versionSummary } from "@/lib/sets";
import { formatSeconds, formatTime, profileName } from "@/lib/shots";
import { cn } from "@/lib/utils";
import { RollbackButton } from "./RollbackButton";

/**
 * A Set's history as an experiment log, newest made first.
 *
 * Each entry is one whole experiment: what changed against the parent, what you
 * were trying, what you predicted it would do, the shots it produced with how
 * you labelled them, and how the prediction turned out. The diff was always the
 * point of this page; the prediction and the outcome are what turn "here is
 * what I changed" into "here is what I expected and here is what happened".
 *
 * Version 1 shows no diff because it is a baseline rather than a change to
 * anything. A version the Set went back past is muted but fully readable: it
 * was a real attempt, it is just not the line being brewed. The order is the
 * server's, by when each version was made; a name says nothing about age, so
 * the "current" marker follows the version the Set is on, wherever it sits, and
 * each entry names the version it grew from. A time the Set went back to an
 * older version is one line of its own at its date, not a version.
 *
 * Version 1 of a Set still being designed has no recipe at all, and says so in
 * words — "being designed — no recipe yet" — rather than as a row of empty
 * fields, a prediction button and an outcome control for an experiment nobody
 * can run yet. Once the recipe is accepted it reads as any version 1.
 */
/**
 * "Add a prediction" / "Edit prediction", and the reason when it is closed.
 *
 * A recorded grade locks the words it grades: the server answers a write with
 * `VERSION_HAS_OUTCOME`, so the button is disabled and the way out — clear the
 * grade first — is a sentence on the page tied to it, not a tooltip on a
 * control nothing can focus. Its own component so it can own a `useId`: the
 * timeline renders one of these per version.
 */
function PredictionEditorButton({
  version,
  onEdit,
}: {
  version: SetVersionDetail["version"];
  onEdit: () => void;
}) {
  const lockId = useId();
  const locked = version.outcome != null;
  return (
    <>
      <Button
        type="button"
        size="sm"
        variant="ghost"
        disabled={locked}
        aria-describedby={locked ? lockId : undefined}
        onClick={onEdit}
      >
        {version.prediction ? "Edit prediction" : "Add a prediction"}
      </Button>
      {locked ? (
        <p id={lockId} className="text-muted-foreground text-xs" data-testid="prediction-locked">
          Clear the outcome first — it grades this prediction as it is written.
        </p>
      ) : null}
    </>
  );
}

/**
 * What a missing side of a change reads as.
 *
 * "cleared" is a person's doing: they emptied a field on the form. A change
 * that rode in with a profile has no form and nobody to do it — the profile
 * that arrived simply states no temperature, and saying "cleared" would accuse
 * somebody of an edit they did not make.
 */
function absent(fromProfile: boolean, side: "before" | "after"): string {
  if (fromProfile) return "no temperature stated";
  return side === "before" ? "not set" : "cleared";
}

/** Whether a version states nothing of a recipe: no profile, grind, dose or yield. */
function hasNoRecipe(version: SetVersionDetail["version"]): boolean {
  return (
    !version.profile_version_id &&
    !version.grind_setting &&
    !version.dose_g &&
    !version.target_yield_g
  );
}

export function VersionTimeline({
  setId,
  versions,
  judgements,
  reverts = [],
  designing = false,
}: {
  setId: number;
  versions: SetVersionDetail[];
  judgements: Record<string, ShotJudgement>;
  /** Each time the Set went back to an earlier version, for the log's own lines. */
  reverts?: SetRevertRow[];
  /** The Set is still being designed: its version 1 may have no recipe yet. */
  designing?: boolean;
}) {
  // Origins are a closed vocabulary like every other one on these pages, so the
  // words come from `GET /api/vocab`. The slug is the fallback while that is in
  // flight, which reads as a slightly terse label rather than as a blank.
  const vocab = useVocabulary();
  const originLabel = (origin: string) =>
    vocab.data?.origins.find((term) => term.value === origin)?.label ?? origin;
  const [editing, setEditing] = useState<number | null>(null);
  const current = currentVersion(versions)?.version.id;

  // A plain function, not a component: it reads the state above, and a
  // component defined here would remount (and lose an open editor) per render.
  const renderVersion = (entry: SetVersionDetail) => (
    <li
      key={entry.version.id}
      data-testid="version-entry"
      data-version={entry.version.version_label}
      data-dead-end={entry.dead_end ? "yes" : "no"}
      className={cn("rounded-lg border border-border", entry.dead_end && "opacity-60")}
    >
      <div className="flex flex-wrap items-baseline justify-between gap-2 border-border border-b px-3 py-2">
        <div className="flex items-baseline gap-2">
          <span className="font-medium text-sm tabular-nums">{entry.version.version_label}</span>
          {entry.version.id === current ? (
            <Badge data-testid="current-version">current</Badge>
          ) : null}
          {/* Where it grew from: a name only identifies, so the fork
                  history is said here. The first version grew from nothing. */}
          {entry.version.parent_version_label ? (
            <span
              className="shrink-0 whitespace-nowrap text-muted-foreground text-xs"
              data-testid="version-parent"
            >
              from {entry.version.parent_version_label}
            </span>
          ) : null}
          <span className="text-muted-foreground text-sm">
            {versionSummary(entry.version)}
            {versionRatio(entry.version) ? ` · ${versionRatio(entry.version)}` : ""}
          </span>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {entry.dead_end ? (
            <Badge
              variant="outline"
              data-testid="dead-end"
              title="The Set went back past this version."
            >
              dead end
            </Badge>
          ) : null}
          {entry.version.profile_label ? (
            <Badge variant="outline">{entry.version.profile_label}</Badge>
          ) : null}
          <Badge variant="ghost" className="text-muted-foreground">
            {originLabel(entry.version.origin)}
          </Badge>
          <span className="text-muted-foreground text-xs">
            {formatTime(entry.version.created_at)}
          </span>
        </div>
      </div>

      <div className="space-y-2 px-3 py-2">
        {entry.changes.length > 0 ? (
          <ul className="flex flex-wrap gap-2" data-testid="version-changes">
            {entry.changes.map((change) => (
              <li
                key={change.field}
                data-testid={change.from_profile ? "change-from-profile" : "change"}
                data-field={change.field}
                className="inline-flex items-center gap-1 rounded-md bg-muted/60 px-2 py-0.5 text-xs"
              >
                <span className="text-muted-foreground">{change.label}</span>
                {/* "93 °C → —" reads as a rendering bug. A field that was
                        set and is now unset is a deliberate change, and the
                        word is what makes it one — except for a change that
                        came with the profile, where nobody cleared anything:
                        the new profile simply does not state the number. */}
                <span className="tabular-nums line-through opacity-60">
                  {change.before ?? absent(change.from_profile, "before")}
                </span>
                <ArrowRight className="size-3" aria-hidden="true" />
                <span className="font-medium tabular-nums">
                  {change.after ?? absent(change.from_profile, "after")}
                </span>
                {/* Said in words rather than shown in a colour: this is the
                        one line in the log with no field behind it on the form,
                        and a reader who cannot see why it is there reads it as
                        a bug. */}
                {change.from_profile ? (
                  <span className="text-muted-foreground">· from the profile</span>
                ) : null}
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-muted-foreground text-xs" data-testid="version-baseline">
            {entry.version.parent_version_id
              ? "Nothing in the recipe changed — only the intent."
              : "The starting point."}
          </p>
        )}

        {entry.version.intent ? (
          <p className="text-sm" data-testid="version-intent">
            {entry.version.intent}
          </p>
        ) : null}

        <div className="space-y-1" data-testid="version-prediction">
          {entry.version.prediction ? (
            <p className="text-sm">
              <span className="text-muted-foreground text-xs">
                Version prediction
                {entry.version.compares_to_version_label
                  ? ` · compared to ${entry.version.compares_to_version_label}`
                  : ""}
              </span>
              <br />
              {entry.version.prediction}
            </p>
          ) : (
            <p className="text-muted-foreground text-xs">No prediction.</p>
          )}
          {/* Only while the version has no shots: the server refuses the
                  write afterwards, so offering the field would be a lie. */}
          {entry.version.shot_count === 0 ? (
            editing === entry.version.id ? (
              <VersionPredictionEditor
                setId={setId}
                version={entry.version}
                versions={versions}
                onDone={() => setEditing(null)}
              />
            ) : (
              <PredictionEditorButton
                version={entry.version}
                onEdit={() => setEditing(entry.version.id)}
              />
            )
          ) : null}
        </div>

        <VersionOverrides
          setId={setId}
          versionId={entry.version.id}
          profileVersionId={entry.version.profile_version_id ?? null}
        />

        {entry.version.restores_version_label ? (
          <p className="text-muted-foreground text-xs" data-testid="version-restores">
            Restores {entry.version.restores_version_label}. Nothing was sent to the machine.
          </p>
        ) : null}

        {/* The room where this change was argued, one click from the log.
                Through the Chat page's open-or-continue link, so it is the
                same conversation Discuss opens and not a second one. */}
        <p className="text-xs" data-testid="version-chat">
          <Link
            to={`/chat?set=${setId}&version=${entry.version.id}`}
            className="text-muted-foreground underline underline-offset-2"
          >
            Chat about {entry.version.version_label}
          </Link>
        </p>

        {/* A version an agent proposed was argued somewhere *else*: in the
                conversation about the version before it. That room holds the
                reasoning this entry is the result of, and it is a different
                thread from the one above. */}
        {entry.chat_thread_id ? (
          <p className="text-xs" data-testid="version-proposed-in">
            <Link
              to={`/chat?thread=${entry.chat_thread_id}`}
              className="text-muted-foreground underline underline-offset-2"
            >
              Proposed in a conversation — read it
            </Link>
          </p>
        ) : null}

        <p className="text-muted-foreground text-xs" data-testid="version-labels">
          {/* This version's shots, not the whole Set's: the count beside
                  the link is this version's, and a link that widened to the
                  Set would answer a question nobody asked here. */}
          <Link
            to={`/shots?set=${setId}&version=${entry.version.id}`}
            className="underline underline-offset-2"
          >
            {entry.version.shot_count} shot{entry.version.shot_count === 1 ? "" : "s"}
          </Link>
          {labelSummary(entry.labels) ? ` · ${labelSummary(entry.labels)}` : ""}
        </p>

        {entry.shots.length > 0 ? (
          <ul className="divide-y divide-border" data-testid="version-shots">
            {entry.shots.map((shot) => {
              const judgement = judgements[String(shot.id)];
              return (
                <li key={shot.id}>
                  <Link
                    to={`/shots/${shot.id}`}
                    className="flex flex-wrap items-center gap-x-3 gap-y-1 py-1.5 text-sm hover:bg-muted/40"
                  >
                    <span className="w-40 shrink-0 tabular-nums">
                      {formatTime(shot.started_at)}
                    </span>
                    <span className="min-w-0 flex-1 truncate text-muted-foreground">
                      {profileName(shot)}
                    </span>
                    <span className="tabular-nums">{formatSeconds(shot.duration_ms)}</span>
                    <CheckBadge checks={shot.checks} />
                    <RatingStars rating={judgement?.rating ?? shot.rating ?? null} />
                  </Link>
                </li>
              );
            })}
          </ul>
        ) : (
          <p className="text-muted-foreground text-xs">No shots on this version yet.</p>
        )}

        {/* Only where there is a prediction to be evidence for. Open by
                default on the one entry that is still a live question: a
                prediction nobody has graded, on a version with shots to grade
                it with. */}
        {entry.evidence ? (
          <VersionEvidence
            evidence={entry.evidence}
            versionLabel={entry.version.version_label}
            defaultOpen={entry.version.outcome_state === "open" && entry.evidence.this.shots > 0}
          />
        ) : null}

        <VersionOutcomeControl
          setId={setId}
          version={entry.version}
          gradable={entry.labels.keep + entry.labels.improve > 0}
          proposed={entry.outcome_proposal ?? null}
        />

        {entry.version.id !== current ? (
          <RollbackButton
            setId={setId}
            versionId={entry.version.id}
            versionLabel={entry.version.version_label}
            label="Go back to this version"
            icon={<Undo2 className="size-3.5" aria-hidden="true" />}
          />
        ) : null}
      </div>
    </li>
  );

  return (
    <ol className="space-y-3" data-testid="version-timeline">
      {logItems(versions, reverts).map((item) =>
        item.kind === "revert" ? (
          <RevertLine key={`revert-${item.revert.id}`} revert={item.revert} />
        ) : designing && versions.length === 1 && hasNoRecipe(item.entry.version) ? (
          <BeingDesigned key={item.entry.version.id} setId={setId} entry={item.entry} />
        ) : (
          renderVersion(item.entry)
        ),
      )}
    </ol>
  );
}

/**
 * One time the Set went back to an earlier version, at its date.
 *
 * A line of the log rather than a card: no version was written, so there is no
 * recipe, prediction or shots to show, only what happened and the person's
 * reason if they gave one.
 */
function RevertLine({ revert }: { revert: SetRevertRow }) {
  return (
    <li
      data-testid="revert-entry"
      className="flex flex-wrap items-baseline gap-x-2 gap-y-1 rounded-lg border border-border border-dashed px-3 py-2 text-sm"
    >
      <Undo2 className="size-3.5 self-center text-muted-foreground" aria-hidden="true" />
      <span>
        Went back to <span className="font-medium tabular-nums">{revert.to_version_label}</span>{" "}
        (from <span className="tabular-nums">{revert.from_version_label}</span>)
      </span>
      <span className="text-muted-foreground text-xs">{formatTime(revert.created_at)}</span>
      {revert.note ? (
        <span className="basis-full text-muted-foreground text-xs" data-testid="revert-note">
          {revert.note}
        </span>
      ) : null}
    </li>
  );
}

/**
 * Version 1 while the recipe is still being worked out in its conversation.
 *
 * The link is the version's own conversation, the one the design is happening
 * in. The shots line stays only when somebody filed a shot here by hand, which
 * is also what stops the design being discarded.
 */
function BeingDesigned({ setId, entry }: { setId: number; entry: SetVersionDetail }) {
  return (
    <li
      data-testid="version-entry"
      data-version={entry.version.version_label}
      data-designing="yes"
      className="rounded-lg border border-border border-dashed"
    >
      <div className="flex flex-wrap items-baseline justify-between gap-2 border-border border-b border-dashed px-3 py-2">
        <div className="flex items-baseline gap-2">
          <span className="font-medium text-sm tabular-nums">{entry.version.version_label}</span>
          <span className="text-muted-foreground text-sm" data-testid="version-being-designed">
            being designed — no recipe yet
          </span>
        </div>
        <span className="text-muted-foreground text-xs">
          {formatTime(entry.version.created_at)}
        </span>
      </div>
      <div className="space-y-2 px-3 py-2">
        <p className="text-muted-foreground text-xs">
          The agent works the first recipe out with you in this version's conversation. Accepting
          its card fills this version in; so does recording one by hand.
        </p>
        <p className="text-xs" data-testid="version-chat">
          <Link
            to={`/chat?set=${setId}&version=${entry.version.id}`}
            className="text-muted-foreground underline underline-offset-2"
          >
            Continue designing {entry.version.version_label}
          </Link>
        </p>
        {entry.version.shot_count > 0 ? (
          <p className="text-muted-foreground text-xs" data-testid="version-labels">
            <Link
              to={`/shots?set=${setId}&version=${entry.version.id}`}
              className="underline underline-offset-2"
            >
              {entry.version.shot_count} shot{entry.version.shot_count === 1 ? "" : "s"}
            </Link>{" "}
            filed here by hand
          </p>
        ) : null}
      </div>
    </li>
  );
}
