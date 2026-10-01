import { AlertTriangle, Check, ListPlus, Sparkles, Trash2 } from "lucide-react";
import { useId, useRef, useState } from "react";
import type { BoardRow, DraftLanding, ProfileDraft, StopConditionChange } from "@/api/types";
import { clampChangesOf, stopConditionChangesOf } from "@/api/types";
import { ProfileDiff } from "@/components/drafts/ProfileDiff";
import { MajorChoice } from "@/components/sets/MajorChoice";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { usePutOnBoard } from "@/hooks/useBoard";
import { useDiscardDraft, useProfileDraft, useRefineDraft } from "@/hooks/useDrafts";
import { outcomeLines } from "@/lib/draftOutcome";
import { formatTime } from "@/lib/shots";

/**
 * One draft in the queue, with everything a person needs before deciding.
 *
 * The order on the card is the order the decision is made in: what it claims to
 * have changed, what it *actually* changed, what the safety policy moved on the
 * way in, and — if it moved a stop condition — the warning and the checkbox
 * that stand between it and the board.
 *
 * **One button decides.** "Put on the board" approves the draft and makes it a profile's next
 * version in one action; the next sync puts it on the machine. Before the board has taken the
 * machine's profiles there is nothing to put it on, so the card says what makes that happen
 * (the Writes switch on, then a sync) and offers only what the server would not refuse:
 * refine and discard. Nothing on this card writes to the machine, and a profile that is on it
 * is taken off from the board (delete it, or go back a version), not from here.
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

/**
 * Buttons whose labels run to a sentence ("Put on the board and record it as v1.2 of …").
 * The base button is `shrink-0 whitespace-nowrap`, which in a flex row makes the card wider
 * than a phone: these shrink, wrap and stay inside the card.
 */
export const WRAP_BUTTON = "h-auto min-h-8 min-w-0 max-w-full shrink whitespace-normal text-left";

export function DraftCard({
  draft,
  adopted = false,
  boardUnknown = false,
  boardRow = null,
  writesOn = true,
  landing,
}: {
  draft: ProfileDraft;
  /**
   * Whether the board has taken the machine's profiles yet. Until it has, a draft cannot be
   * put on it, and the card says what makes that happen.
   */
  adopted?: boolean;
  /** The board could not be read, so a put is not offered. */
  boardUnknown?: boolean;
  /** The board row this draft is on, waiting for the next sync. */
  boardRow?: BoardRow | null;
  /** Whether the Writes switch is on (a sync writes nothing otherwise). */
  writesOn?: boolean;
  /**
   * Where a put of this draft would land, as the server works it out with the code a put
   * runs: a new profile, or a new version of a board profile, and whether that profile
   * already holds a newer draft (so putting this one would undo it).
   */
  landing?: DraftLanding;
}) {
  const onBoard = boardRow !== null;
  // With the board adopted a draft that has not been put is put in one action (which also
  // approves it): a drafted one or one approved before that was so.
  const open = !onBoard && (draft.status === "draft" || draft.status === "approved");
  const waiting = adopted && open;
  // A put the server would refuse is not offered: it would undo a newer version, the board
  // already has a profile with the label it would carry, or its exact document is there.
  const alreadyOnBoard = landing?.already_on_board_label ?? null;
  const plainBlocked =
    alreadyOnBoard !== null ||
    landing?.plain.holds_newer_draft === true ||
    (landing?.plain.taken_label ?? null) !== null;
  const setBlocked =
    alreadyOnBoard !== null ||
    landing?.for_set?.holds_newer_draft === true ||
    (landing?.for_set?.taken_label ?? null) !== null;
  const detail = useProfileDraft(draft.id);
  const put = usePutOnBoard();
  // One put per click: a second click before the first has re-rendered the buttons disabled
  // would send a second request for the same draft.
  const putting = useRef(false);
  const putOnBoard = (body: { draftId: number; setId?: number; major?: boolean }) => {
    if (putting.current) return;
    putting.current = true;
    // Putting a draft on the board approves it, so the stop-condition acknowledgement the
    // approval needed is sent with the put.
    put.mutate(acknowledged ? { ...body, acknowledgeStopChanges: true } : body, {
      onSettled: () => {
        putting.current = false;
      },
    });
  };
  const discard = useDiscardDraft();
  const refine = useRefineDraft();

  const [acknowledged, setAcknowledged] = useState(false);
  const [notes, setNotes] = useState("");
  const [refining, setRefining] = useState(false);
  // "Major change" on the put for the draft's Set: the person's answer once
  // given, the agent's suggestion until then. A tuned copy is dialling in,
  // so without either it is a minor version.
  const [majorChoice, setMajorChoice] = useState<boolean | null>(null);
  const major = majorChoice ?? draft.suggest_major;
  const acknowledgeId = useId();
  const notesId = useId();

  const stopChanges = stopConditionChangesOf(draft);
  const clamps = clampChangesOf(draft);
  const badge = STATUS_BADGE[draft.status] ?? { label: draft.status, variant: "outline" as const };
  const busy = put.isPending || discard.isPending || refine.isPending;
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
          because "the prediction is recorded" is only true of a put for this
          Set — putting it on the board for another one records none. */}
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

      {needsAcknowledgement && (draft.status === "draft" || draft.status === "approved") ? (
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
            diff above is against a version the machine no longer holds. Putting it on the board
            means the next sync puts this version beside whatever was changed there. Drafting again
            from the current profile is usually what you want.
          </p>
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
              ? ` It is on the display as ${draft.pushed_device_profile_id}, from an earlier push the app no longer makes; remove it on the display.`
              : " Its copy has already been removed from the display."}
          </p>
        </div>
      ) : null}

      {draft.status === "pushed" && draft.replaced_by_draft_id != null ? (
        <p className="mt-3 text-muted-foreground text-xs" data-testid="draft-replaced">
          A later version replaced this profile on the machine.
        </p>
      ) : null}

      {draft.status === "pushed" && draft.replaced_by_draft_id == null ? (
        <p className="mt-3 text-muted-foreground text-xs" data-testid="draft-pushed">
          On the machine as <span className="font-mono">{draft.pushed_device_profile_id}</span>. The
          machine keeps brewing with whatever it had selected, unless that was the profile this one
          replaced. To take it off, delete the profile from the board, or go back to its previous
          version there.
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
        {draft.status === "approved" && onBoard ? (
          <p className="w-full text-sm" data-testid="draft-on-board">
            <Check className="mr-1 inline size-3.5" aria-hidden="true" />
            On the board. It reaches the machine{" "}
            {writesOn ? "on the next sync" : "once writes are turned on"}.
          </p>
        ) : null}

        {draft.status === "approved" && boardUnknown ? (
          <p className="w-full text-muted-foreground text-sm" data-testid="draft-board-unknown">
            The board can't be read right now, so this can't be put on the machine until it can.
          </p>
        ) : null}

        {/* Nothing to put it on yet: say what makes that happen, and offer only refine and
            discard, which the server does not refuse. */}
        {!adopted && open ? (
          <p className="w-full text-sm" data-testid="draft-board-not-adopted">
            Profiles reach the machine once the Writes switch is on and a sync has taken the
            machine's profiles onto the board. Then this can be put on it.
          </p>
        ) : null}

        {waiting && landing ? (
          <div className="w-full space-y-1 text-sm" data-testid="draft-landing">
            {alreadyOnBoard !== null ? (
              <p data-testid="draft-already-on-board">
                {`This exact profile is already on the board as ${alreadyOnBoard}, so there is nothing to put. Discard this draft, or refine it into something else.`}
              </p>
            ) : (
              <>
                <p>{landingSentence(landing.plain, forSet !== null ? "Without the Set, " : "")}</p>
                {landing.for_set ? (
                  <p>{landingSentence(landing.for_set, "Recorded for the Set, ")}</p>
                ) : null}
              </>
            )}
          </div>
        ) : null}

        {/* A draft that has not been put can be turned down; one waiting on the board cannot
            be discarded from under it. A failed one (an old push that did not verify) can be
            cleared from the queue, its copy on the display stays for the person to remove. */}
        {open || draft.status === "failed" ? (
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

        {/* One click: the put approves the draft and carries what approval carried (the
            stop-condition acknowledgement) and what a push carried: the Set whose next version
            it becomes, and whether that is a major change. */}
        {waiting && forSet !== null ? (
          <>
            {!setBlocked && forSet.minorLabel !== null && forSet.majorLabel !== null ? (
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
            {setBlocked ? null : (
              <Button
                size="sm"
                disabled={busy || (needsAcknowledgement && !acknowledged)}
                data-testid="put-on-board-for-set"
                className={WRAP_BUTTON}
                onClick={() => putOnBoard({ draftId: draft.id, setId: forSet.id, major })}
              >
                <ListPlus className="size-3.5" aria-hidden="true" />
                Put on the board and record it as{" "}
                {(major ? forSet.majorLabel : forSet.minorLabel) ?? "a new version"} of{" "}
                {forSet.name}
              </Button>
            )}
            {plainBlocked ? null : (
              <Button
                size="sm"
                variant="outline"
                disabled={busy || (needsAcknowledgement && !acknowledged)}
                data-testid="put-on-board"
                onClick={() => putOnBoard({ draftId: draft.id })}
                className={WRAP_BUTTON}
              >
                Put on the board without recording it on the Set
              </Button>
            )}
          </>
        ) : null}

        {waiting && !plainBlocked && forSet === null ? (
          <Button
            size="sm"
            disabled={busy || (needsAcknowledgement && !acknowledged)}
            data-testid="put-on-board"
            onClick={() => putOnBoard({ draftId: draft.id })}
            className={WRAP_BUTTON}
          >
            <ListPlus className="size-3.5" aria-hidden="true" />
            Put on the board
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
    </li>
  );
}

/**
 * Where a put lands, as a sentence. `lead` is empty for a draft without a Set, "Without the
 * Set, " or "Recorded for the Set, " when the card shows both landings; the sentence then
 * continues in lower case.
 */
function landingSentence(
  landing: {
    row_id?: number | null;
    row_label?: string | null;
    holds_newer_draft?: boolean;
    taken_label?: string | null;
    revives_label?: string | null;
  },
  lead: string,
): string {
  const sentence = (lowerCase: string, upperCase: string) =>
    lead === "" ? upperCase : `${lead}${lowerCase}`;
  if (landing.taken_label) {
    return sentence(
      `the board already has ${landing.taken_label}; refine this draft from it, or discard it.`,
      `The board already has ${landing.taken_label}; refine this draft from it, or discard it.`,
    );
  }
  if (landing.holds_newer_draft) {
    const name = landing.row_label ?? "this profile";
    return sentence(
      `putting this on the board would undo a newer version of ${name} that is already there. Refine the newer one, or discard this draft.`,
      `Putting this on the board would undo a newer version of ${name} that is already there. Refine the newer one, or discard this draft.`,
    );
  }
  let where: string;
  if (landing.row_id != null) {
    where = `replaces ${landing.row_label ?? "a profile"} on the board, as its next version`;
  } else if (landing.revives_label) {
    where = `goes back on the board as ${landing.revives_label}`;
  } else {
    where = "goes on the board as a new profile";
  }
  return lead === "" ? `It ${where}.` : `${lead}it ${where}.`;
}

/**
 * Where the prediction ends up, said from what the archive holds rather than from the button
 * that was pressed: a draft put on the board without recording it on its Set, or for another
 * one, recorded nothing, and the line must not claim otherwise.
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
        ? `Recorded as ${recorded} of ${where} when it reached the machine for it.`
        : `It reached the machine without being recorded on ${where}, so this prediction was not recorded.`;
  } else if (draft.status === "approved" && boardRow !== null) {
    // The choice has been made: say what it was, not what could still be chosen.
    line =
      boardRow.pending_set_id != null
        ? `It is recorded as ${
            (boardRow.pending_major ? draft.set_next_major_label : draft.set_next_minor_label) ??
            "the next version"
          } of ${where} when the next sync puts this draft on the machine.`
        : `Put on the board without recording it on ${where}, so this prediction will not be recorded.`;
  } else if (draft.status === "draft" || draft.status === "approved") {
    line = adopted
      ? "It is recorded on the Set when the next sync puts this draft on the machine, if you put it on the board for that Set."
      : "It is recorded on the Set when you put this draft on the board for that Set and a sync then puts it on the machine, and not before.";
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
