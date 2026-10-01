import { AlertTriangle, Check, ListPlus, Sparkles, Trash2, Undo2, Upload } from "lucide-react";
import { useId, useState } from "react";
import type { BoardRow, ProfileDraft, StopConditionChange } from "@/api/types";
import { clampChangesOf, stopConditionChangesOf } from "@/api/types";
import { ProfileDiff } from "@/components/drafts/ProfileDiff";
import { MajorChoice } from "@/components/sets/MajorChoice";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { usePutOnBoard } from "@/hooks/useBoard";
import {
  useApproveDraft,
  useDiscardDraft,
  useProfileDraft,
  usePushDraft,
  useRefineDraft,
  useRollbackDraft,
} from "@/hooks/useDrafts";
import { outcomeLines } from "@/lib/draftOutcome";
import { formatTime } from "@/lib/shots";

/**
 * One draft in the queue, with everything a person needs before deciding.
 *
 * The order on the card is the order the decision is made in: what it claims to
 * have changed, what it *actually* changed, what the safety policy moved on the
 * way in, and — if it moved a stop condition — the warning and the checkbox
 * that stand between it and the machine.
 *
 * The acknowledgement is local state, not a stored preference and not a default.
 * It resets every time the card is re-rendered from a fresh row, which is the
 * point: it is a statement about *this* draft's yield change, and a checkbox
 * that remembered itself would be one nobody reads.
 */

const STATUS_BADGE: Record<
  string,
  { label: string; variant: "default" | "secondary" | "outline" }
> = {
  draft: { label: "drafted", variant: "secondary" },
  approved: { label: "approved", variant: "default" },
  pushed: { label: "on the machine", variant: "default" },
  failed: { label: "did not verify", variant: "outline" },
  discarded: { label: "discarded", variant: "outline" },
  superseded: { label: "overtaken", variant: "outline" },
};

export function DraftCard({
  draft,
  adopted = false,
  boardUnknown = false,
  boardRow = null,
  writesOn = true,
  overtaken = false,
}: {
  draft: ProfileDraft;
  /**
   * The board is the machine's master: a pull writes it, so there is no push or rollback
   * (the server refuses both) and an approved draft is put on the board instead.
   */
  adopted?: boolean;
  /** The board could not be read, so neither a push nor a put is offered. */
  boardUnknown?: boolean;
  /** The board row this draft is on, waiting for the next pull. */
  boardRow?: BoardRow | null;
  /** Whether the Writes switch is on (a pull writes nothing otherwise). */
  writesOn?: boolean;
  /** A newer draft of the same profile is on the board: putting this one would undo it. */
  overtaken?: boolean;
}) {
  const onBoard = boardRow !== null;
  const detail = useProfileDraft(draft.id);
  const approve = useApproveDraft();
  const push = usePushDraft();
  const put = usePutOnBoard();
  const rollback = useRollbackDraft();
  const discard = useDiscardDraft();
  const refine = useRefineDraft();

  const [acknowledged, setAcknowledged] = useState(false);
  const [allowStale, setAllowStale] = useState(false);
  const [notes, setNotes] = useState("");
  const [refining, setRefining] = useState(false);
  // "Major change" on the push for the draft's Set: the person's answer once
  // given, the agent's suggestion until then. A pushed draft tunes a profile,
  // so without either it is a minor version.
  const [majorChoice, setMajorChoice] = useState<boolean | null>(null);
  const major = majorChoice ?? draft.suggest_major;
  const acknowledgeId = useId();
  const staleId = useId();
  const notesId = useId();

  const stopChanges = stopConditionChangesOf(draft);
  const clamps = clampChangesOf(draft);
  const badge = STATUS_BADGE[draft.status] ?? { label: draft.status, variant: "outline" as const };
  const busy =
    approve.isPending ||
    push.isPending ||
    put.isPending ||
    rollback.isPending ||
    discard.isPending ||
    refine.isPending;
  const needsAcknowledgement = stopChanges.length > 0 && !draft.acknowledged_stop_changes;
  // The generated type leaves these optional; absent and null mean the same.
  const forSet =
    draft.set_id != null && draft.set_name != null
      ? {
          id: draft.set_id,
          name: draft.set_name,
          minorLabel: draft.set_next_minor_label ?? null,
          majorLabel: draft.set_next_major_label ?? null,
        }
      : null;

  return (
    <li
      className="rounded-lg border border-border p-4"
      data-testid="draft-card"
      data-status={draft.status}
    >
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div className="min-w-0">
          <p className="truncate font-medium text-sm">{draft.draft_label ?? "Untitled draft"}</p>
          <p className="text-muted-foreground text-xs">
            from {draft.base_label ?? "an unknown profile"} · {formatTime(draft.created_at)}
            {/* History only: a draft made from an analysis before the per-shot
                analysis was retired. Its id now names a carried review, so it
                is not shown as a link or a number. */}
            {draft.source_analysis_id ? " · from an analysis (now a review)" : ""}
          </p>
        </div>
        <Badge variant={badge.variant}>{badge.label}</Badge>
      </div>

      {draft.change_summary ? <p className="mt-2 text-sm">{draft.change_summary}</p> : null}

      {/* A draft proposed inside a Set's conversation is an experiment on that
          Set, and this is what it claims. It says where the claim lands,
          because "the prediction is recorded" is only true of a push for this
          Set — pushing it for another one records none. */}
      {draft.prediction ? (
        <div
          className="mt-2 rounded-md border border-border bg-muted/40 p-2"
          data-testid="draft-prediction"
        >
          <p className="text-muted-foreground text-xs">
            Prediction for {draft.set_name ?? "its Set"}
            {draft.compares_to_version_label
              ? ` · compared to ${draft.compares_to_version_label}`
              : ""}
          </p>
          <p className="text-sm">{draft.prediction}</p>
          <PredictionLanding draft={draft} adopted={adopted} boardRow={boardRow} />
        </div>
      ) : null}

      <div className="mt-3">
        <h4 className="mb-1 font-medium text-sm">What changes</h4>
        {detail.isPending ? (
          <Skeleton className="h-12 w-full" />
        ) : (
          <ProfileDiff
            base={(detail.data?.base_profile ?? null) as Record<string, unknown> | null}
            draft={(detail.data?.draft_profile ?? null) as Record<string, unknown> | null}
          />
        )}
      </div>

      {clamps.length > 0 ? (
        <div className="mt-3" data-testid="draft-clamps">
          <h4 className="mb-1 font-medium text-sm">Moved into range by the safety policy</h4>
          <ul className="space-y-0.5">
            {clamps.map((clamp) => (
              <li key={clamp.path} className="text-sm">
                <span className="font-mono text-muted-foreground text-xs">{clamp.path}</span>{" "}
                {clamp.before} → {clamp.after}
                <span className="text-muted-foreground"> — {clamp.reason}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {stopChanges.length > 0 ? <StopConditionWarning changes={stopChanges} /> : null}

      {!draft.base_is_current && draft.status !== "pushed" && draft.status !== "discarded" ? (
        <div
          className="mt-3 rounded-md border border-status-warn/40 bg-status-warn/10 p-3"
          data-testid="stale-base-warning"
        >
          <p className="flex items-center gap-1.5 font-medium text-sm text-status-warn-text">
            <AlertTriangle className="size-3.5" aria-hidden="true" />
            The profile this came from has changed on the machine
          </p>
          <p className="mt-1 text-status-warn-text text-xs">
            {draft.base_label ?? "It"} was edited on the display after this draft was made, so the
            diff above is against a version the machine no longer holds.{" "}
            {adopted
              ? "Putting it on the board means the next pull puts this version beside whatever was changed there."
              : "Pushing anyway proposes undoing whatever was changed there."}{" "}
            Drafting again from the current profile is usually what you want.
          </p>
          {draft.status === "approved" && !adopted ? (
            <label
              className="mt-2 flex items-start gap-2 text-sm text-status-warn-text"
              htmlFor={staleId}
              data-testid="allow-stale-base"
            >
              <input
                id={staleId}
                type="checkbox"
                className="mt-1 size-4"
                checked={allowStale}
                onChange={(event) => setAllowStale(event.target.checked)}
              />
              <span>Push it anyway.</span>
            </label>
          ) : null}
        </div>
      ) : null}

      {draft.status === "failed" ? (
        <div
          className="mt-3 rounded-md border border-border bg-muted/50 p-3"
          data-testid="draft-failed"
        >
          <p className="flex items-center gap-1.5 font-medium text-sm">
            <AlertTriangle className="size-3.5 text-status-warn-text" aria-hidden="true" />
            The machine did not store what was sent
          </p>
          <p className="mt-1 text-muted-foreground text-xs">
            {draft.error ?? "The profile read back differently from the one that went out."}
            {draft.pushed_device_profile_id
              ? ` It is on the display as ${draft.pushed_device_profile_id}${adopted ? "." : "; rolling back deletes that copy."}`
              : " Its copy has already been removed from the display."}
          </p>
        </div>
      ) : null}

      {draft.status === "pushed" && draft.replaced_by_draft_id != null ? (
        <p className="mt-3 text-muted-foreground text-xs" data-testid="draft-replaced">
          A later push replaced this profile on the machine, so there is nothing left to roll back
          here.
        </p>
      ) : null}

      {draft.status === "pushed" && draft.replaced_by_draft_id == null ? (
        <p className="mt-3 text-muted-foreground text-xs" data-testid="draft-pushed">
          On the machine as <span className="font-mono">{draft.pushed_device_profile_id}</span>. The
          machine keeps brewing with whatever it had selected, unless that was the profile this one
          replaced.
        </p>
      ) : null}

      {outcomeLines(draft).length > 0 ? (
        <ul
          className="mt-2 list-disc space-y-0.5 pl-5 text-muted-foreground text-xs"
          data-testid="draft-outcome"
        >
          {outcomeLines(draft).map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
      ) : null}

      <div className="mt-3 flex flex-wrap items-center gap-2">
        {draft.status === "draft" ? (
          <>
            <Button
              size="sm"
              disabled={busy || (needsAcknowledgement && !acknowledged)}
              data-testid="approve-draft"
              onClick={() => approve.mutate({ id: draft.id, acknowledgeStopChanges: acknowledged })}
            >
              <Check className="size-3.5" aria-hidden="true" />
              Approve
            </Button>
            <Button
              size="sm"
              variant="ghost"
              disabled={busy}
              data-testid="discard-draft"
              onClick={() => discard.mutate(draft.id)}
            >
              <Trash2 className="size-3.5" aria-hidden="true" />
              Discard
            </Button>
          </>
        ) : null}

        {/* A draft made in a Set's conversation is pushed for that Set unless
            the person says otherwise: that push is what records the new
            version and the prediction, and it is the one the prediction
            block promises. Trying it without touching the Set stays one
            click away. A draft whose Set has been removed has nowhere to be
            recorded and gets the plain push. */}
        {draft.status === "approved" && onBoard ? (
          <p className="w-full text-sm" data-testid="draft-on-board">
            <Check className="mr-1 inline size-3.5" aria-hidden="true" />
            On the board. It reaches the machine{" "}
            {writesOn ? "on the next pull" : "once writes are turned on"}.
          </p>
        ) : null}

        {draft.status === "approved" && boardUnknown ? (
          <p className="w-full text-muted-foreground text-sm" data-testid="draft-board-unknown">
            The board can't be read right now, so this can't be put on the machine until it can.
          </p>
        ) : null}

        {draft.status === "approved" && adopted && !onBoard && overtaken ? (
          <p className="w-full text-sm" data-testid="draft-overtaken">
            A newer version of this profile is already on the board, so putting this one there would
            undo it. Refine the newer one, or discard this draft.
          </p>
        ) : null}

        {draft.status === "approved" && adopted && !onBoard ? (
          <Button
            size="sm"
            variant="ghost"
            disabled={busy}
            data-testid="discard-draft"
            onClick={() => discard.mutate(draft.id)}
          >
            <Trash2 className="size-3.5" aria-hidden="true" />
            Discard
          </Button>
        ) : null}

        {/* With the board adopted a pull is the only thing that writes a profile, so an
            approved draft goes on the board, carrying what the push carried: the Set whose
            next version it becomes, and whether that is a major change. */}
        {draft.status === "approved" && adopted && !onBoard && !overtaken && forSet !== null ? (
          <>
            {forSet.minorLabel !== null && forSet.majorLabel !== null ? (
              <div className="w-full">
                <MajorChoice
                  checked={major}
                  onChange={setMajorChoice}
                  minorLabel={forSet.minorLabel}
                  majorLabel={forSet.majorLabel}
                  reason={draft.suggest_major ? draft.major_reason : ""}
                  disabled={busy}
                />
              </div>
            ) : null}
            <Button
              size="sm"
              disabled={busy}
              data-testid="put-on-board-for-set"
              className="h-auto min-h-8 whitespace-normal text-left"
              onClick={() => put.mutate({ draftId: draft.id, setId: forSet.id, major })}
            >
              <ListPlus className="size-3.5" aria-hidden="true" />
              Put on the board and record it as{" "}
              {(major ? forSet.majorLabel : forSet.minorLabel) ?? "a new version"} of {forSet.name}
            </Button>
            <Button
              size="sm"
              variant="outline"
              disabled={busy}
              data-testid="put-on-board"
              onClick={() => put.mutate({ draftId: draft.id })}
            >
              Put on the board without recording it on the Set
            </Button>
          </>
        ) : null}

        {draft.status === "approved" && adopted && !onBoard && !overtaken && forSet === null ? (
          <Button
            size="sm"
            disabled={busy}
            data-testid="put-on-board"
            onClick={() => put.mutate({ draftId: draft.id })}
          >
            <ListPlus className="size-3.5" aria-hidden="true" />
            Put on the board
          </Button>
        ) : null}

        {draft.status === "approved" && !adopted && !boardUnknown && forSet !== null ? (
          <>
            {forSet.minorLabel !== null && forSet.majorLabel !== null ? (
              <div className="w-full">
                <MajorChoice
                  checked={major}
                  onChange={setMajorChoice}
                  minorLabel={forSet.minorLabel}
                  majorLabel={forSet.majorLabel}
                  reason={draft.suggest_major ? draft.major_reason : ""}
                  disabled={busy}
                />
              </div>
            ) : null}
            <Button
              size="sm"
              disabled={busy || (!draft.base_is_current && !allowStale)}
              data-testid="push-draft-for-set"
              // A long Set name wraps inside the button rather than pushing the
              // card wider than a phone.
              className="h-auto min-h-8 whitespace-normal text-left"
              onClick={() =>
                push.mutate({ id: draft.id, setId: forSet.id, allowStaleBase: allowStale, major })
              }
            >
              <Upload className="size-3.5" aria-hidden="true" />
              Push to the machine and record it as{" "}
              {(major ? forSet.majorLabel : forSet.minorLabel) ?? "a new version"} of {forSet.name}
            </Button>
            <Button
              size="sm"
              variant="outline"
              disabled={busy || (!draft.base_is_current && !allowStale)}
              data-testid="push-draft"
              onClick={() => push.mutate({ id: draft.id, allowStaleBase: allowStale })}
            >
              Push without recording it on the Set
            </Button>
          </>
        ) : null}

        {draft.status === "approved" && !adopted && !boardUnknown && forSet === null ? (
          <Button
            size="sm"
            disabled={busy || (!draft.base_is_current && !allowStale)}
            data-testid="push-draft"
            onClick={() => push.mutate({ id: draft.id, allowStaleBase: allowStale })}
          >
            <Upload className="size-3.5" aria-hidden="true" />
            Push to the machine
          </Button>
        ) : null}

        {/* Offered for a push that did not verify *and* for one that did: a
            profile you pushed and then thought better of is the same deletion.
            A failed draft stays failed afterwards; a pushed one becomes
            discarded, because the machine no longer has it. */}
        {!adopted &&
        !boardUnknown &&
        (draft.status === "failed" || draft.status === "pushed") &&
        draft.pushed_device_profile_id &&
        draft.replaced_by_draft_id == null ? (
          <Button
            size="sm"
            variant="destructive"
            disabled={busy}
            data-testid="rollback-draft"
            onClick={() => rollback.mutate(draft.id)}
          >
            <Undo2 className="size-3.5" aria-hidden="true" />
            {draft.replaced_device_profile_id
              ? "Roll back: restore the previous profile"
              : "Delete it from the machine"}
          </Button>
        ) : null}

        {draft.status === "draft" || draft.status === "approved" || draft.status === "failed" ? (
          <Button
            size="sm"
            variant="outline"
            disabled={busy}
            data-testid="refine-draft"
            onClick={() => setRefining((open) => !open)}
          >
            <Sparkles className="size-3.5" aria-hidden="true" />
            Refine
          </Button>
        ) : null}
      </div>

      {refining ? (
        <div className="mt-3 space-y-2" data-testid="refine-form">
          <label className="font-medium text-sm" htmlFor={notesId}>
            What should be different?
          </label>
          <Textarea
            id={notesId}
            rows={3}
            placeholder="Still too harsh at the start — soften the ramp instead of the peak."
            value={notes}
            onChange={(event) => setNotes(event.target.value)}
          />
          <p className="text-muted-foreground text-xs">
            This draft is superseded rather than edited, so what you asked for at each attempt stays
            readable.
          </p>
          <Button
            size="sm"
            disabled={busy || notes.trim().length === 0}
            data-testid="submit-refine"
            onClick={() => {
              refine.mutate({ id: draft.id, notes });
              setNotes("");
              setRefining(false);
            }}
          >
            Draft again
          </Button>
        </div>
      ) : null}

      {needsAcknowledgement && draft.status === "draft" ? (
        <label
          className="mt-3 flex items-start gap-2 text-sm"
          htmlFor={acknowledgeId}
          data-testid="acknowledge-stop-changes"
        >
          <input
            id={acknowledgeId}
            type="checkbox"
            className="mt-1 size-4"
            checked={acknowledged}
            onChange={(event) => setAcknowledged(event.target.checked)}
          />
          <span>
            I understand this changes how much coffee ends up in the cup, not just how it is pulled.
          </span>
        </label>
      ) : null}
    </li>
  );
}

/**
 * Where the prediction ends up, said from what the archive holds rather than
 * from the button that was pressed: a draft pushed without recording it on its
 * Set, or pushed for another one, recorded nothing, and the line must not claim
 * otherwise. Nothing is said once the draft can no longer be pushed at all.
 */
function PredictionLanding({
  draft,
  adopted,
  boardRow,
}: {
  draft: ProfileDraft;
  adopted: boolean;
  boardRow: BoardRow | null;
}) {
  const where = draft.set_name ?? "the Set";
  let line: string | null = null;
  if (draft.status === "pushed") {
    const recorded = draft.recorded_version_label ?? null;
    line =
      recorded !== null
        ? `Recorded as ${recorded} of ${where} when this was pushed for it.`
        : `Pushed without recording it on ${where}, so this prediction was not recorded.`;
  } else if (draft.status === "approved" && boardRow !== null) {
    // The choice has been made: say what it was, not what could still be chosen.
    line =
      boardRow.pending_set_id != null
        ? `It is recorded as ${
            (boardRow.pending_major ? draft.set_next_major_label : draft.set_next_minor_label) ??
            "the next version"
          } of ${where} when the next pull puts this draft on the machine.`
        : `Put on the board without recording it on ${where}, so this prediction will not be recorded.`;
  } else if (draft.status === "draft" || draft.status === "approved") {
    line = adopted
      ? "It is recorded on the Set when the next pull puts this draft on the machine, if you put it on the board for that Set."
      : "It is recorded on the Set when you push this draft for that Set, and not before.";
  }
  return line === null ? null : (
    <p className="mt-1 text-muted-foreground text-xs" data-testid="draft-prediction-landing">
      {line}
    </p>
  );
}

function StopConditionWarning({ changes }: { changes: StopConditionChange[] }) {
  return (
    <div
      className="mt-3 rounded-md border border-status-warn/40 bg-status-warn/10 p-3"
      data-testid="stop-condition-warning"
    >
      <p className="flex items-center gap-1.5 font-medium text-sm text-status-warn-text">
        <AlertTriangle className="size-3.5" aria-hidden="true" />
        This changes when the machine stops
      </p>
      <p className="mt-1 text-status-warn-text text-xs">
        A stop condition decides when the pump stops, which decides how much coffee is in the cup.
        Everything else in a profile changes how a shot is pulled; this changes how much.
      </p>
      <ul className="mt-2 space-y-0.5">
        {changes.map((change) => (
          <li
            key={`${change.phase_index}-${change.target_type}-${change.kind}`}
            className="text-sm"
          >
            <span className="font-mono text-xs">
              phase {change.phase_index + 1} · {change.phase_name}
            </span>{" "}
            <span className="text-muted-foreground">{change.kind}</span> {change.target_type}{" "}
            {change.before ? `${change.before.operator} ${change.before.value}` : "—"} →{" "}
            {change.after ? `${change.after.operator} ${change.after.value}` : "—"}
          </li>
        ))}
      </ul>
    </div>
  );
}
