import { useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Check, LoaderCircle, X } from "lucide-react";
import { useCallback, useId, useRef, useState } from "react";
import { Link } from "react-router-dom";
import type { DraftStanding, StopConditionChange } from "@/api/types";
import { clampChangesOf, stopConditionChangesOf } from "@/api/types";
import { approvedProfileMessage, declinedMessage, useTellAgent } from "@/components/chat/tellAgent";
import { ProfileDiff } from "@/components/drafts/ProfileDiff";
import { ProfileSummary } from "@/components/drafts/ProfileSummary";
import { ProfileCurve } from "@/components/profiles/ProfileCurve";
import { ProposalNameField, useProposalName } from "@/components/profiles/ProposalName";
import { MajorChoice } from "@/components/sets/MajorChoice";
import { useSyncOwner } from "@/components/sync/SyncOwner";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { usePutOnBoard } from "@/hooks/useBoard";
import { useDeviceStatus } from "@/hooks/useDeviceStatus";
import { useDiscardDraft, useDraftStanding } from "@/hooks/useDrafts";
import { attempt } from "@/lib/mutations";
import { hasCurve, profileCurve } from "@/lib/profileCurve";
import { queryKeys } from "@/lib/queryKeys";
import { formatTime } from "@/lib/shots";
import { syncUnavailableWhy } from "@/lib/sync";
import { cn } from "@/lib/utils";

type Json = Record<string, unknown>;

/**
 * Buttons whose labels run to a sentence ("Approve as v1.2 of <Set> and sync"). The base button
 * is `shrink-0 whitespace-nowrap`, which in a flex row makes the card wider than a phone: these
 * shrink, wrap and stay inside it.
 */
export const WRAP_BUTTON = "h-auto min-h-8 min-w-0 max-w-full shrink whitespace-normal text-left";

/** How long a turn-down note may be, as the route caps it. */
const NOTE_MAX = 500;

/** The key a sync started by this card's draft is asked for under. */
export const syncKeyOf = (draftId: number) => `draft-${draftId}`;

/**
 * Whether approving this waiting proposal would make a profile of its own, whose name is then the
 * person's to choose. The put's own landing says so (the Set's when the proposal is for one).
 */
export function landsOnNewProfile(standing: DraftStanding): boolean {
  const { draft, landing } = standing;
  const target =
    draft.set_id != null && landing?.for_set != null ? landing.for_set : landing?.plain;
  return (target?.row_id ?? null) === null;
}

/**
 * A proposed version of a profile, and where it stands, from proposed to on the machine.
 *
 * One card in two places: the conversation where the agent proposed it, and inside its profile on
 * the Profiles page. It is drawn from the server's standing read (`GET /profile-drafts/{id}/standing`)
 * and nothing else: every sentence about the machine is the server's `reason`, and the web
 * composes none. A person answers it here with one click:
 *
 * - **Approve** (Writes off) makes this version the profile's active one (and the Set's next
 *   version, in a Set chat) and switches the profile on. Same request as ever, `POST
 *   /api/profile-board`; nothing reaches the machine.
 * - **Approve and sync** (Writes on) is that and then the same sync the top bar's button starts,
 *   through the app's one sync owner. The sync does not select the profile on the machine.
 * - **Decline** reveals an optional one-line note. The note is not stored; in the chat it is the
 *   person's message to the agent.
 *
 * In the chat the answer is also told to the agent (`TellAgentContext`); on the Profiles page
 * there is no conversation to tell.
 */
export function ProfileProposalCard({
  draftId,
  place,
  summary,
  proposedAs,
}: {
  draftId: number;
  place: "chat" | "profiles";
  /** The tool result's summary, shown until the standing read lands (the chat only). */
  summary?: string;
  /** The name the agent proposed, from its tool call, to say "proposed as" after a rename. */
  proposedAs?: string | null;
}) {
  const standing = useDraftStanding(draftId);
  if (standing.data === undefined) {
    return (
      <div
        className="min-w-0 rounded-md border border-border bg-muted/30 p-3"
        data-testid="profile-proposal-unsettled"
        data-draft-id={draftId}
        data-state={standing.isError ? "unreadable" : "loading"}
      >
        {standing.isError ? (
          <p className="break-words text-sm" role="alert">
            This proposal could not be read right now.
          </p>
        ) : summary ? (
          <p className="break-words text-sm">{summary}</p>
        ) : (
          <Skeleton className="h-12 w-full" />
        )}
      </div>
    );
  }
  return (
    <Card
      // A card handed another proposal starts from its own: no half-typed name or note follows.
      key={draftId}
      standing={standing.data}
      place={place}
      proposedAs={proposedAs ?? null}
    />
  );
}

function Card({
  standing,
  place,
  proposedAs,
}: {
  standing: DraftStanding;
  place: "chat" | "profiles";
  proposedAs: string | null;
}) {
  const { draft, state } = standing;
  const name = draft.draft_label ?? "Untitled";
  const owner = useSyncOwner();
  const syncing = state === "approved" && owner.waitingFor(syncKeyOf(draft.id));
  // The name the agent gave it, when the person approved it under another: remembered here for
  // the card that did the renaming, and passed in for one drawn again from the transcript.
  const [renamedFrom, setRenamedFrom] = useState<string | null>(null);
  const was = renamedFrom ?? proposedAs;
  // Set by a successful approval: the card that replaces the waiting one takes focus on its
  // heading, so a keyboard user does not land on the page body.
  const focusTitle = useRef(false);

  const tone =
    state === "waiting"
      ? "border-status-warn/40 bg-status-warn/5"
      : state === "not_on_machine"
        ? "border-status-bad/40 bg-status-bad/5"
        : state === "approved" || state === "on_machine"
          ? "border-primary/40 bg-primary/5"
          : "border-border bg-muted/30";

  return (
    <div
      className={cn("@container min-w-0 space-y-3 rounded-md border p-3", tone)}
      data-testid="profile-proposal"
      data-draft-id={draft.id}
      data-state={state}
      data-syncing={syncing ? "yes" : "no"}
    >
      {state === "waiting" ? (
        <Waiting
          standing={standing}
          place={place}
          onRenamed={setRenamedFrom}
          onApproved={() => {
            focusTitle.current = true;
          }}
        />
      ) : (
        <Answered
          standing={standing}
          syncing={syncing}
          name={name}
          was={was}
          focusRef={focusTitle}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------------------------
// Waiting for a person
// ---------------------------------------------------------------------------------------------

function Waiting({
  standing,
  place,
  onRenamed,
  onApproved,
}: {
  standing: DraftStanding;
  place: "chat" | "profiles";
  onRenamed: (oldName: string | null) => void;
  onApproved: () => void;
}) {
  const { draft, landing, profile, active_profile: active } = standing;
  const put = usePutOnBoard();
  const discard = useDiscardDraft();
  const owner = useSyncOwner();
  const tellAgent = useTellAgent();
  const device = useDeviceStatus();
  const queryClient = useQueryClient();
  const noteId = useId();
  const noteFieldId = useId();
  // One request per click: a second click before the first has re-rendered would send a second.
  const acting = useRef(false);
  const [declining, setDeclining] = useState(false);
  const [note, setNote] = useState("");
  const [majorChoice, setMajorChoice] = useState<boolean | null>(null);
  const major = majorChoice ?? draft.suggest_major;

  const proposedName = draft.draft_label ?? "";
  const stopChanges = stopConditionChangesOf(draft);
  const clamps = clampChangesOf(draft);
  const forSet =
    draft.set_id != null && draft.set_name != null && landing?.for_set != null
      ? {
          id: draft.set_id,
          name: draft.set_name,
          minorLabel: draft.set_next_minor_label ?? null,
          majorLabel: draft.set_next_major_label ?? null,
        }
      : null;
  // A proposal that would be a profile of its own: its name is the person's to choose.
  const isNew = landsOnNewProfile(standing);
  // A profile written from scratch is shown whole, never as a diff against the baseline it is
  // stored with, and has no stops it changes: whether or not its name is still the person's.
  const fresh = isNew || draft.is_new === true;
  const header = isNew ? "Proposed new profile" : `Proposed change · ${proposedName || "Untitled"}`;
  const [typed, setTyped] = useState(proposedName);
  const name = useProposalName(draft.id, typed, isNew);
  const { trimmed, checking } = name;
  // The server's sentence for the name as proposed, from the landing: shown at once, until the
  // check of what is in the field answers.
  const landingSentence = isNew
    ? ((forSet !== null ? landing?.for_set : landing?.plain)?.refused ?? null)
    : null;
  const proposedUnchanged = trimmed === proposedName.trim();
  const checkProblem = name.problem ?? (proposedUnchanged && checking ? landingSentence : null);
  // A refusal of the put, for the name that was on screen when it was sent: shown under the field
  // once, and gone as soon as the name changes.
  const nameProblem = checkProblem;
  // A refused put that carried this name: the name is asked of the server again (below) and its
  // sentence, for the person, is what the field shows. If the name is fine after all, something
  // else refused the put, and its words go under the buttons.
  const putRefusedThisName =
    isNew &&
    put.isError &&
    put.variables?.draftId === draft.id &&
    put.variables.label !== undefined &&
    put.variables.label === trimmed;
  const writes = standing.writes_enabled;
  // Whether this click starts a sync: Writes on and a machine to reach. Until the machine's
  // status is read the label assumes it can be reached, as the top bar's button does not.
  const configured = device.data?.configured ?? true;
  const connected = device.data?.connected ?? true;
  const syncs = writes && configured && connected;
  const noSyncWhy = writes ? syncUnavailableWhy(configured, connected) : null;
  const sync = syncs ? " and sync" : "";

  const setLabel =
    forSet !== null ? ((major ? forSet.majorLabel : forSet.minorLabel) ?? null) : null;
  const approveWords =
    forSet !== null
      ? `Approve as ${setLabel ?? "a new version"} of ${forSet.name}${sync}`
      : isNew && trimmed && nameProblem === null
        ? `Approve${sync} — add “${trimmed}”`
        : `Approve${sync}`;
  const approveWithoutSet =
    place === "profiles" && forSet !== null
      ? `Approve without recording it on ${forSet.name}${sync}`
      : null;

  // A press that has succeeded is answered for good: the card is about to become the answered
  // card, and the moments before the standing is read again must not offer a second press.
  const answered =
    (put.isSuccess && put.variables?.draftId === draft.id) ||
    (discard.isSuccess && discard.variables === draft.id);
  const answering = put.isPending || discard.isPending || answered;
  const approveBlocked = answering || nameProblem !== null || checking;

  const approve = async (recordOnSet: boolean) => {
    if (acting.current) return;
    acting.current = true;
    try {
      const row = await attempt(() =>
        put.mutateAsync({
          draftId: draft.id,
          ...(recordOnSet && forSet !== null ? { setId: forSet.id, major } : {}),
          ...(isNew ? { label: trimmed } : {}),
          // The card says it is syncing, and the sync's own notification follows; a refusal is
          // shown by the card, under the name or under the buttons.
          quiet: syncs,
          quietError: true,
        }),
      );
      if (row === undefined) {
        // The name may have been taken since it was checked: ask again, and show the server's
        // sentence for the person, never the put's.
        if (isNew) {
          void queryClient.invalidateQueries({
            queryKey: queryKeys.nameChecks.one(draft.id, trimmed),
          });
        }
        return;
      }
      onApproved();
      const renamed = isNew && trimmed !== proposedName ? proposedName : null;
      if (renamed !== null) onRenamed(renamed);
      tellAgent?.(
        approvedProfileMessage({
          name: isNew ? trimmed : proposedName,
          forSet:
            recordOnSet && forSet !== null && setLabel !== null
              ? { versionLabel: setLabel, setName: forSet.name }
              : null,
          newVersion: !isNew,
          proposedAs: renamed,
        }),
      );
      // Only the click on Approve and sync starts a sync, and only after the put succeeded.
      if (syncs) await owner.startSync(syncKeyOf(draft.id));
    } finally {
      acting.current = false;
    }
  };

  const decline = async () => {
    if (acting.current) return;
    acting.current = true;
    try {
      // On the Profiles page nobody is listening: there is no note to take.
      const reason = place === "chat" ? note.trim() : "";
      const done = await attempt(() => discard.mutateAsync(draft.id));
      if (done !== undefined) tellAgent?.(declinedMessage(reason));
    } finally {
      acting.current = false;
    }
  };

  // Only the refusal of a press on *this* proposal: the page keeps one mutation for them all.
  const refusal =
    put.isError &&
    put.variables?.draftId === draft.id &&
    (put.variables.label === undefined || (putRefusedThisName && !checking && nameProblem === null))
      ? put.error.message
      : discard.isError && discard.variables === draft.id
        ? discard.error.message
        : null;

  const curves = profile ?? null;
  const both = !fresh && hasCurve(active) && hasCurve(curves);
  const xMax = Math.max(profileCurve(active)?.xMax ?? 0, profileCurve(curves)?.xMax ?? 0);

  return (
    <>
      <div className="flex flex-wrap items-baseline justify-between gap-x-2 gap-y-1">
        <span className="min-w-0 break-words font-medium text-sm">{header}</span>
        <span className="text-muted-foreground text-xs">{formatTime(draft.created_at)}</span>
      </div>

      {isNew ? <ProposalNameField typed={typed} onChange={setTyped} problem={nameProblem} /> : null}

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

      <div
        className={cn("grid min-w-0 gap-3", both && "@2xl:grid-cols-2")}
        data-testid="proposal-curves"
      >
        {!fresh && hasCurve(active) ? (
          <div className="min-w-0" data-testid="proposal-active-curve">
            <ProfileCurve profile={active} title="Active version" xMax={xMax} />
          </div>
        ) : null}
        {hasCurve(curves) ? (
          <div className="min-w-0" data-testid="proposal-proposed-curve">
            <ProfileCurve profile={curves} title="Proposed" xMax={both ? xMax : undefined} />
          </div>
        ) : null}
      </div>

      <div>
        <h4 className="mb-1 font-medium text-sm">{fresh ? "New profile" : "What changes"}</h4>
        {profile === null || profile === undefined ? (
          <Skeleton className="h-12 w-full" />
        ) : active != null && !fresh ? (
          <ProfileDiff
            base={active as Json}
            draft={profile as Json}
            empty="Nothing changed from the active version."
          />
        ) : (
          <ProfileSummary profile={profile as Json} />
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

      {forSet !== null && forSet.minorLabel !== null && forSet.majorLabel !== null ? (
        <MajorChoice
          checked={major}
          onChange={setMajorChoice}
          minorLabel={forSet.minorLabel}
          majorLabel={forSet.majorLabel}
          reason={draft.suggest_major ? draft.major_reason : ""}
          disabled={answering}
        />
      ) : null}

      <div className="flex flex-wrap items-center gap-2">
        <Button
          size="sm"
          aria-disabled={approveBlocked}
          data-testid="approve-proposal"
          className={cn(WRAP_BUTTON, "aria-disabled:opacity-50")}
          // Not `disabled`: a second press of a double click would land on a disabled button
          // and drop focus to the page; the guard sends one request either way.
          onMouseDown={(event) => event.preventDefault()}
          onClick={() => {
            if (approveBlocked) return;
            void approve(true);
          }}
        >
          <Check className="size-3.5" aria-hidden="true" />
          {approveWords}
        </Button>

        {approveWithoutSet !== null ? (
          <Button
            size="sm"
            variant="outline"
            aria-disabled={approveBlocked}
            data-testid="approve-proposal-without-set"
            className={cn(WRAP_BUTTON, "aria-disabled:opacity-50")}
            onMouseDown={(event) => event.preventDefault()}
            onClick={() => {
              if (approveBlocked) return;
              void approve(false);
            }}
          >
            {approveWithoutSet}
          </Button>
        ) : null}

        <Button
          size="sm"
          variant="ghost"
          aria-disabled={answering}
          // The note's form exists only in the chat, where the agent reads it.
          {...(place === "chat" ? { "aria-expanded": declining, "aria-controls": noteId } : {})}
          data-testid="decline-proposal"
          className="aria-disabled:opacity-50"
          onMouseDown={(event) => event.preventDefault()}
          onClick={() => {
            if (answering) return;
            if (place === "chat") setDeclining((open) => !open);
            else void decline();
          }}
        >
          <X className="size-3.5" aria-hidden="true" />
          Decline
        </Button>
      </div>

      {!writes ? (
        <p className="break-words text-muted-foreground text-xs" data-testid="proposal-writes-off">
          Writes are off (top bar), so it goes to the machine at the first sync after you turn them
          on.
        </p>
      ) : noSyncWhy !== null ? (
        <p className="break-words text-muted-foreground text-xs" data-testid="proposal-no-sync">
          {noSyncWhy}
        </p>
      ) : null}

      {place === "chat" ? (
        <>
          {/* Rendered always and toggled with `hidden`, so `aria-controls` names something a reader
          can reach. The note is optional; it is not stored, and in the chat it is the person's
          message to the agent. */}
          <form
            id={noteId}
            hidden={!declining}
            className="space-y-1"
            data-testid="decline-note"
            onSubmit={(event) => {
              event.preventDefault();
              if (answering) return;
              void decline();
            }}
          >
            <label htmlFor={noteFieldId} className="block text-muted-foreground text-xs">
              Why not? Optional.
              {place === "chat" ? " The agent is told." : ""}
            </label>
            <div className="flex flex-wrap items-center gap-2">
              <input
                id={noteFieldId}
                maxLength={NOTE_MAX}
                value={note}
                placeholder="too aggressive for a light roast"
                className="h-8 min-w-0 flex-1 basis-48 rounded-md border border-input bg-background px-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                onChange={(event) => setNote(event.target.value)}
                onKeyDown={(event) => {
                  // Escape closes the field and declines nothing; it stops here so a page with an
                  // Escape handler of its own does not lose a half-typed note to it.
                  if (event.key === "Escape") {
                    event.stopPropagation();
                    setDeclining(false);
                    setNote("");
                  }
                }}
              />
              <Button
                type="submit"
                size="sm"
                variant="outline"
                aria-disabled={answering}
                className="aria-disabled:opacity-50"
                data-testid="decline-confirm"
                onMouseDown={(event) => event.preventDefault()}
              >
                Decline it
              </Button>
              <Button
                type="button"
                size="sm"
                variant="ghost"
                onClick={() => {
                  setDeclining(false);
                  setNote("");
                }}
              >
                Cancel
              </Button>
            </div>
          </form>
        </>
      ) : null}

      {refusal !== null ? (
        <p
          className="break-words text-destructive text-sm"
          role="alert"
          data-testid="proposal-error"
        >
          {refusal}
        </p>
      ) : null}
    </>
  );
}

// ---------------------------------------------------------------------------------------------
// Answered
// ---------------------------------------------------------------------------------------------

/**
 * Where an approved proposal stands, as a line inside another card (the first recipe's): the
 * server's words for it, no frame of its own. Nothing while the proposal is still waiting, or
 * was never put on the list.
 */
export function ProfileStandingLine({
  draftId,
  proposedAs = null,
}: {
  draftId: number;
  /** The name the agent proposed, when the person approved it under another. */
  proposedAs?: string | null;
}) {
  const standing = useDraftStanding(draftId);
  const owner = useSyncOwner();
  const data = standing.data;
  if (data === undefined || data.state === "waiting" || data.state === "declined") return null;
  return (
    <div className="mt-2 min-w-0 space-y-2" data-testid="profile-standing" data-state={data.state}>
      <Answered
        standing={data}
        syncing={data.state === "approved" && owner.waitingFor(syncKeyOf(draftId))}
        name={data.draft.draft_label ?? "Untitled"}
        was={proposedAs}
        withTime={false}
      />
    </div>
  );
}

function Answered({
  standing,
  syncing,
  name,
  was,
  withTime = true,
  focusRef,
}: {
  standing: DraftStanding;
  syncing: boolean;
  name: string;
  was: string | null;
  /** The proposal's time at the end of the title; a line inside another card leaves it out. */
  withTime?: boolean;
  /** Set by an approval on this card: the title takes focus once. */
  focusRef?: React.MutableRefObject<boolean>;
}) {
  const { draft, state, reason, selected } = standing;
  const curveId = useId();
  const titleRef = useCallback(
    (element: HTMLSpanElement | null) => {
      if (element && focusRef?.current) {
        focusRef.current = false;
        element.focus();
      }
    },
    [focusRef],
  );
  const [curveOpen, setCurveOpen] = useState(false);
  const recorded =
    standing.set_version_label && draft.set_name
      ? ` → ${standing.set_version_label} of ${draft.set_name}`
      : "";
  // "Approved as Gentle Bloom (proposed as Soft Bloom)": only when the person changed the name.
  // Only when the name it was proposed under differs from the stored one: exact, after trimming
  // (a change of case alone is a different name to the person, and to the machine's list).
  const renamed = was !== null && was.trim() !== name.trim() ? ` (proposed as ${was.trim()})` : "";

  let title: string;
  let body: React.ReactNode = null;
  if (state === "approved") {
    title = `✓ Approved${renamed ? " as" : " ·"} ${name}${renamed}${recorded}`;
    body = syncing ? (
      <p className="flex items-center gap-1.5 text-sm" data-testid="proposal-syncing">
        <LoaderCircle className="size-3.5 animate-spin" aria-hidden="true" />
        Syncing with the machine…
      </p>
    ) : (
      <p className="break-words text-sm">{reason}</p>
    );
  } else if (state === "on_machine") {
    title = `✓ On the machine · ${name}${renamed}${recorded}`;
    body = (
      <p className="break-words text-sm">
        {selected === true
          ? "It is the selected profile: your next shot uses it."
          : `Select “${name}” on the machine for your next shot.`}
      </p>
    );
  } else if (state === "not_on_machine") {
    title = `✗ Not on the machine · ${name}${renamed}${recorded}`;
    body = (
      <>
        <p className="break-words text-sm">{reason}</p>
        <Link
          className="inline-block text-sm underline underline-offset-2"
          data-testid="open-in-profiles"
          to={
            draft.draft_version_id != null
              ? `/profiles#version-${draft.draft_version_id}`
              : "/profiles"
          }
        >
          Open in Profiles
        </Link>
      </>
    );
  } else if (state === "declined") {
    title = `Declined · ${name}`;
  } else {
    title = `Replaced · ${name}${renamed}`;
    body = <p className="break-words text-sm">{reason}</p>;
  }

  return (
    <>
      <div className="flex flex-wrap items-baseline justify-between gap-x-2 gap-y-1">
        <span
          ref={titleRef}
          tabIndex={-1}
          className="min-w-0 break-words font-medium text-sm outline-none"
          data-testid="proposal-title"
        >
          {title}
        </span>
        {withTime ? (
          <span className="text-muted-foreground text-xs">{formatTime(draft.created_at)}</span>
        ) : null}
      </div>
      {body}
      {hasCurve(standing.profile) ? (
        <div>
          <Button
            size="sm"
            variant="ghost"
            className="-ml-2"
            aria-expanded={curveOpen}
            aria-controls={curveId}
            data-testid="show-proposal-curve"
            onClick={() => setCurveOpen((open) => !open)}
          >
            {curveOpen ? "Hide the profile curve" : "Show the profile curve"}
          </Button>
          {/* Always rendered, so `aria-controls` resolves; the chart's code loads on first open. */}
          <div id={curveId} hidden={!curveOpen} className="min-w-0">
            {curveOpen ? <ProfileCurve profile={standing.profile} title="Proposed" /> : null}
          </div>
        </div>
      ) : null}
    </>
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
