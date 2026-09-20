import { ArrowRight, Undo2 } from "lucide-react";
import { useId, useState } from "react";
import { Link } from "react-router-dom";
import type { SetVersionDetail, ShotJudgement } from "@/api/types";
import { VersionEvidence } from "@/components/sets/VersionEvidence";
import { VersionOutcomeControl } from "@/components/sets/VersionOutcomeControl";
import { VersionPredictionEditor } from "@/components/sets/VersionPredictionEditor";
import { RatingStars } from "@/components/shots/RatingStars";
import { ScoreBadge } from "@/components/shots/ScoreBadge";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useVocabulary } from "@/hooks/useCatalog";
import { labelSummary, versionRatio, versionSummary } from "@/lib/sets";
import { formatSeconds, formatTime, profileName } from "@/lib/shots";
import { cn } from "@/lib/utils";
import { RollbackButton } from "./RollbackButton";

/**
 * A Set's history as an experiment log, newest first.
 *
 * Each entry is one whole experiment: what changed against the parent, what you
 * were trying, what you predicted it would do, the shots it produced with how
 * you labelled them, and how the prediction turned out. The diff was always the
 * point of this page; the prediction and the outcome are what turn "here is
 * what I changed" into "here is what I expected and here is what happened".
 *
 * Version 1 shows no diff because it is a baseline rather than a change to
 * anything. A version a later roll back stepped over is muted but fully
 * readable: it was a real attempt, it is just not the line being brewed.
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

export function VersionTimeline({
  setId,
  versions,
  judgements,
}: {
  setId: number;
  versions: SetVersionDetail[];
  judgements: Record<string, ShotJudgement>;
}) {
  // Origins are a closed vocabulary like every other one on these pages, so the
  // words come from `GET /api/vocab`. The slug is the fallback while that is in
  // flight, which reads as a slightly terse label rather than as a blank.
  const vocab = useVocabulary();
  const originLabel = (origin: string) =>
    vocab.data?.origins.find((term) => term.value === origin)?.label ?? origin;
  const [editing, setEditing] = useState<number | null>(null);
  const current = versions[0]?.version.id;

  return (
    <ol className="space-y-3" data-testid="version-timeline">
      {versions.map((entry) => (
        <li
          key={entry.version.id}
          data-testid="version-entry"
          data-version={entry.version.version_no}
          data-dead-end={entry.dead_end ? "yes" : "no"}
          className={cn("rounded-lg border border-border", entry.dead_end && "opacity-60")}
        >
          <div className="flex flex-wrap items-baseline justify-between gap-2 border-border border-b px-3 py-2">
            <div className="flex items-baseline gap-2">
              <span className="font-medium text-sm tabular-nums">v{entry.version.version_no}</span>
              <span className="text-muted-foreground text-sm">
                {versionSummary(entry.version)}
                {versionRatio(entry.version) ? ` · ${versionRatio(entry.version)}` : ""}
              </span>
            </div>
            <div className="flex items-center gap-2">
              {entry.dead_end ? (
                <Badge
                  variant="outline"
                  data-testid="dead-end"
                  title="A later roll back went back past this version."
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
                    {entry.version.compares_to_version_no
                      ? ` · compared to v${entry.version.compares_to_version_no}`
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

            {entry.version.restores_version_no ? (
              <p className="text-muted-foreground text-xs" data-testid="version-restores">
                Restores v{entry.version.restores_version_no}. Nothing was sent to the machine.
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
                        <ScoreBadge score={shot.execution_score ?? null} />
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
                versionNo={entry.version.version_no}
                defaultOpen={
                  entry.version.outcome_state === "open" && entry.evidence.this.shots > 0
                }
              />
            ) : null}

            <VersionOutcomeControl
              setId={setId}
              version={entry.version}
              gradable={entry.labels.keep + entry.labels.improve > 0}
            />

            {entry.version.id !== current ? (
              <RollbackButton
                setId={setId}
                versionId={entry.version.id}
                versionNo={entry.version.version_no}
                label="Roll back to this version"
                icon={<Undo2 className="size-3.5" aria-hidden="true" />}
              />
            ) : null}
          </div>
        </li>
      ))}
    </ol>
  );
}
