import { AlertTriangle, ListPlus, Trash2 } from "lucide-react";
import { useRef, useState } from "react";
import type { DraftLanding, ProfileDraft, StopConditionChange } from "@/api/types";
import { clampChangesOf, stopConditionChangesOf } from "@/api/types";
import { ProfileDiff } from "@/components/drafts/ProfileDiff";
import { ProfileSummary } from "@/components/drafts/ProfileSummary";
import { MajorChoice } from "@/components/sets/MajorChoice";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { usePutOnBoard } from "@/hooks/useBoard";
import { useDiscardDraft } from "@/hooks/useDrafts";
import { useSingleFlight } from "@/hooks/useSingleFlight";
import { formatTime } from "@/lib/shots";

type Json = Record<string, unknown>;

/**
 * Buttons whose labels run to a sentence ("Make active and record it as v1.2 of …"). The base
 * button is `shrink-0 whitespace-nowrap`, which in a flex row makes the panel wider than a
 * phone: these shrink, wrap and stay inside it.
 */
export const WRAP_BUTTON = "h-auto min-h-8 min-w-0 max-w-full shrink whitespace-normal text-left";

/**
 * A version somebody proposed (the agent, a Set's conversation, the JSON editor) that waits
 * for a person: what it changes against the profile's active version, what the safety policy
 * moved, the stop-condition change when there is one, the Set it would be recorded on, and the two
 * answers, **Make active** and **Decline**.
 *
 * Making it active is the one click that approves it: it is `POST /api/profile-board` with the
 * Set recording a push carried. Nothing is sent to the machine; the
 * next sync does, and a Set's version is recorded when that sync has put it there.
 */
export function ProposalPanel({
  draft,
  profile,
  base,
  landing,
  isNew = false,
  rowOn = true,
}: {
  draft: ProfileDraft;
  /** The proposed document, or `null` while it loads. */
  profile: Json | null;
  /** The profile's active version's document to diff against; `null` for a new profile. */
  base: Json | null;
  /** Where making it active would land, as the server works it out; `undefined` while unread. */
  landing: DraftLanding | undefined;
  /** A proposed new profile: there is nothing to compare it with. */
  isNew?: boolean;
  /** Whether the profile it joins is on the machine (a Set records it only once it is). */
  rowOn?: boolean;
}) {
  const put = usePutOnBoard();
  const discard = useDiscardDraft();
  const onceDecline = useSingleFlight();
  // One request per click: a second click before the first has re-rendered would send a second.
  const putting = useRef(false);
  const [majorChoice, setMajorChoice] = useState<boolean | null>(null);
  const major = majorChoice ?? draft.suggest_major;

  const stopChanges = stopConditionChangesOf(draft);
  const clamps = clampChangesOf(draft);
  // A profile designed from scratch has no stops it changes: nothing to warn about, and nothing
  // to diff it against (the base it is stored with is only an anchor).
  const fresh = isNew || draft.is_new === true;
  const busy = put.isPending || discard.isPending;
  const alreadyThere = landing?.already_on_board_label ?? null;
  // Every proposal is an independent candidate: another one being made active never blocks this
  // one. Two things leave nothing to make active: a document that is already in the list, and
  // a new or renamed draft whose name is already a profile. The server says the second one
  // (the sentence the put answers with), and there is no Make active to press.
  const refused = landing?.plain.refused ?? landing?.for_set?.refused ?? null;
  const plainBlocked = alreadyThere !== null || refused !== null;
  const setBlocked = alreadyThere !== null || refused !== null;
  const forSet =
    draft.set_id != null && draft.set_name != null && landing?.for_set != null
      ? {
          id: draft.set_id,
          name: draft.set_name,
          minorLabel: draft.set_next_minor_label ?? null,
          majorLabel: draft.set_next_major_label ?? null,
        }
      : null;
  const makeActive = fresh ? "Add to the list" : "Make active";

  const act = (body: { setId?: number; major?: boolean }) => {
    if (putting.current) return;
    putting.current = true;
    put.mutate(
      { draftId: draft.id, ...body },
      {
        onSettled: () => {
          putting.current = false;
        },
      },
    );
  };

  return (
    <div
      className="min-w-0 space-y-3 rounded-md border border-status-warn/40 bg-status-warn/5 p-3"
      data-testid="proposal"
      data-draft-id={draft.id}
    >
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
        <Badge variant="secondary">Proposed</Badge>
        {fresh ? <Badge>New</Badge> : null}
        <span className="min-w-0 break-words font-medium text-sm">
          {draft.draft_label ?? "Untitled"}
        </span>
        <span className="text-muted-foreground text-xs">{formatTime(draft.created_at)}</span>
      </div>

      {draft.change_summary ? <p className="break-words text-sm">{draft.change_summary}</p> : null}

      {draft.prediction ? (
        <div
          className="rounded-md border border-border bg-muted/40 p-2"
          data-testid="proposal-prediction"
        >
          <p className="text-muted-foreground text-xs">
            Prediction for {draft.set_name ?? "its Set"}
            {draft.compares_to_version_label
              ? ` · compared to ${draft.compares_to_version_label}`
              : ""}
          </p>
          <p className="break-words text-sm">{draft.prediction}</p>
        </div>
      ) : null}

      <div>
        <h4 className="mb-1 font-medium text-sm">{fresh ? "New profile" : "What changes"}</h4>
        {profile === null ? (
          <Skeleton className="h-12 w-full" />
        ) : base !== null && !fresh ? (
          <ProfileDiff
            base={base}
            draft={profile}
            empty="Nothing changed from the active version."
          />
        ) : (
          <ProfileSummary profile={profile} />
        )}
      </div>

      {clamps.length > 0 ? (
        <div data-testid="proposal-clamps">
          <h4 className="mb-1 font-medium text-sm">Moved into range by the safety policy</h4>
          <ul className="space-y-0.5">
            {clamps.map((clamp) => (
              <li key={clamp.path} className="break-words text-sm">
                <span className="font-mono text-muted-foreground text-xs">{clamp.path}</span>{" "}
                {clamp.before} → {clamp.after}
                <span className="text-muted-foreground"> — {clamp.reason}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {!fresh && stopChanges.length > 0 ? <StopConditionWarning changes={stopChanges} /> : null}

      {landing === undefined ? (
        <p className="text-muted-foreground text-sm" data-testid="proposal-landing-pending">
          Checking where it would land…
        </p>
      ) : alreadyThere !== null ? (
        <p className="break-words text-sm" data-testid="proposal-already-there">
          This exact profile is already in the list as {alreadyThere}, so there is nothing to make
          active. Decline this one, or edit a copy into something else.
        </p>
      ) : refused !== null ? (
        <p className="break-words text-sm" data-testid="proposal-name-taken">
          {refused} Decline this one, or edit a copy under another name.
        </p>
      ) : null}

      {forSet !== null && !rowOn ? (
        <p className="text-muted-foreground text-xs" data-testid="proposal-set-off">
          This profile is off the machine, so {forSet.name} records the version only after a sync
          has put it there.
        </p>
      ) : null}

      <div className="flex flex-wrap items-center gap-2">
        {forSet !== null &&
        !setBlocked &&
        forSet.minorLabel !== null &&
        forSet.majorLabel !== null ? (
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

        {landing !== undefined && forSet !== null && !setBlocked ? (
          <Button
            size="sm"
            disabled={busy}
            data-testid="make-proposal-active-for-set"
            className={WRAP_BUTTON}
            onClick={() => act({ setId: forSet.id, major })}
          >
            <ListPlus className="size-3.5" aria-hidden="true" />
            {makeActive} and record it as{" "}
            {(major ? forSet.majorLabel : forSet.minorLabel) ?? "a new version"} of {forSet.name}
          </Button>
        ) : null}

        {landing !== undefined && !plainBlocked ? (
          <Button
            size="sm"
            variant={forSet !== null && !setBlocked ? "outline" : "default"}
            disabled={busy}
            data-testid="make-proposal-active"
            className={WRAP_BUTTON}
            onClick={() => act({})}
          >
            {forSet !== null && !setBlocked ? null : (
              <ListPlus className="size-3.5" aria-hidden="true" />
            )}
            {forSet !== null && !setBlocked
              ? `${makeActive} without recording it on the Set`
              : makeActive}
          </Button>
        ) : null}

        <Button
          size="sm"
          variant="ghost"
          disabled={busy}
          data-testid="decline-proposal"
          onClick={() => onceDecline((release) => discard.mutate(draft.id, { onSettled: release }))}
        >
          <Trash2 className="size-3.5" aria-hidden="true" />
          Decline
        </Button>
      </div>
    </div>
  );
}

function StopConditionWarning({ changes }: { changes: StopConditionChange[] }) {
  return (
    <div
      className="rounded-md border border-status-warn/40 bg-status-warn/10 p-3"
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
            className="break-words text-sm"
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
