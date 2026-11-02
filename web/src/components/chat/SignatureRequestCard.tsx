import { Check, X } from "lucide-react";
import { useId, useState } from "react";
import { ApiClientError } from "@/api/client";
import type { SignatureRequest, SignatureRequestChange } from "@/api/types";
import { useChatThreadId } from "@/components/chat/tellAgent";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useAnswerSignatureRequest, useSignatureRequests } from "@/hooks/useSignatureRequests";
import { useSingleFlight } from "@/hooks/useSingleFlight";
import { attempt } from "@/lib/mutations";
import { formatTime } from "@/lib/shots";

/**
 * A change to a profile's signature the agent asked for, and the two buttons that answer it.
 *
 * What an expectation in force says is what every shot on the profile is checked against, so the
 * agent cannot replace, retire or re-tier one, or put back one the person rejected. It asks, with
 * its reason for each change, and **this card changes nothing until Approve is pressed**. Approve
 * makes the changes with what the Profiles page already has: a replaced or retired expectation
 * becomes a rejection with the agent's reason, and its Restore stays. Decline changes nothing, and
 * may say why.
 *
 * Read live from the conversation's own requests rather than rendered from the tool's output,
 * which is the moment it was called: the card is read again whenever the conversation is scrolled
 * to, after the request may have been answered in another tab or gone stale because the person
 * rejected a row it names on the Profiles page. The answer is not sent to the agent as a message;
 * it is told at the start of its next turn, from the record.
 */

export type SignatureRequestCardProps = {
  requestId: number;
  profileVersionId: number;
  /** What the tool said, shown until the live row has been read. */
  fallback: { profileLabel: string; changes: string[] };
};

/** A failed press, said in words the person can act on. */
export function signatureRequestFailure(error: Error): string {
  if (error instanceof ApiClientError) {
    if (error.code === "SIGNATURE_REQUEST_DECIDED") {
      return "This request was already answered, here in another tab. The card will show how once it refreshes.";
    }
    if (error.code === "SIGNATURE_REQUEST_STALE") {
      // The server names the cause ("You rejected … on the Profiles page.") in its message.
      return `${error.sentence} Nothing was changed.`;
    }
    if (error.code === "SIGNATURE_REQUEST_IS_A_DRAFTS") {
      return "This request came with a profile draft: approve or decline the draft instead.";
    }
  }
  return error.message;
}

export function SignatureRequestCard({
  requestId,
  profileVersionId,
  fallback,
}: SignatureRequestCardProps) {
  const requests = useSignatureRequests(useChatThreadId(), profileVersionId);
  const row = requests.data?.items.find((item) => item.id === requestId);
  if (!row) {
    return (
      <div className="m-2 mt-0 min-w-0" data-testid="propose-card-signature_request">
        <div className="min-w-0 rounded-md border border-primary/40 bg-primary/5 p-2 text-sm">
          <p className="break-words">
            Asked for a change to the signature: {fallback.profileLabel}
          </p>
          <ul className="mt-1 space-y-0.5">
            {fallback.changes.map((line) => (
              <li key={line} className="break-words text-muted-foreground text-xs">
                {line}
              </li>
            ))}
          </ul>
          <span className="block text-muted-foreground text-xs">waiting for you</span>
        </div>
      </div>
    );
  }
  return (
    <div className="m-2 mt-0 min-w-0" data-testid="propose-card-signature_request">
      <LiveCard row={row} />
    </div>
  );
}

const KIND_WORDS: Record<SignatureRequestChange["kind"], string> = {
  replace: "Replace",
  retire: "Retire",
  tier: "Tier",
  put_back: "Put back",
  put_back_override: "Put back limit",
};

function LiveCard({ row }: { row: SignatureRequest }) {
  const answer = useAnswerSignatureRequest();
  const threadId = useChatThreadId();
  const noteId = useId();
  // One request per click: a second click before React has re-rendered would send a second.
  const singleFlight = useSingleFlight();
  const [declining, setDeclining] = useState(false);
  const [note, setNote] = useState("");

  // What the server said to the last press on this card, before the lists it invalidated have
  // been read again.
  const answered =
    answer.data?.request.id === row.id && !answer.isPending ? answer.data.request : null;
  const shown = answered ?? row;
  const waiting = shown.status === "waiting";
  const pressed = answer.isPending || (answer.isSuccess && answer.variables?.requestId === row.id);
  // A refusal is said until the card has been read again: once it shows the request as answered
  // or no longer open, that text says it, and the same sentence is not repeated under it.
  const failure =
    waiting && answer.variables?.requestId === row.id && answer.error ? answer.error : null;

  const send = (choice: "approve" | "decline") => {
    if (pressed) return;
    singleFlight((release) => {
      void attempt(() =>
        answer.mutateAsync({
          requestId: row.id,
          answer: choice,
          reason: choice === "decline" ? note.trim() : undefined,
          threadId,
        }),
      ).finally(release);
    });
  };

  // The profile, and which of its versions the request is about (the Profiles page's own name
  // for it); a Recipe version has a name of its own, so the two are never both "v1".
  const where = shown.version_label
    ? `${shown.profile_label} · profile ${shown.version_label}`
    : shown.profile_label;

  return (
    <div
      className="@container min-w-0 rounded-lg border border-primary/40 bg-primary/5 p-3"
      data-testid="signature-request-card"
      data-status={shown.status}
    >
      <div className="mb-2 flex flex-wrap items-baseline justify-between gap-x-2 gap-y-1">
        <span className="min-w-0 break-words font-medium text-sm" data-testid="request-title">
          Signature change · {where}
        </span>
        <span className="text-muted-foreground text-xs">{formatTime(shown.created_at)}</span>
      </div>

      <ul className="space-y-3">
        {shown.changes.map((change, index) => (
          // A request's changes never reorder, and two may read alike: the position is the key.
          // biome-ignore lint/suspicious/noArrayIndexKey: stable, ordered, never reordered
          <li key={index} className="min-w-0" data-testid="request-change">
            <ChangeLines change={change} />
          </li>
        ))}
      </ul>

      {waiting && shown.loosens_open_prediction ? (
        <p className="mt-2 break-words text-sm text-status-warn-text" data-testid="request-loosens">
          This loosens a check that {shown.loosens_open_prediction}'s open prediction is graded
          under.
        </p>
      ) : null}

      {waiting ? (
        <>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <Button
              size="sm"
              aria-disabled={pressed}
              data-testid="approve-request"
              className="aria-disabled:opacity-50"
              // Not `disabled`: a second press of a double click would land on a disabled button
              // and drop focus to the page; the guard sends one request either way.
              onMouseDown={(event) => event.preventDefault()}
              onClick={() => void send("approve")}
            >
              <Check className="size-3.5" aria-hidden="true" />
              Approve
            </Button>
            <Button
              size="sm"
              variant="outline"
              aria-disabled={pressed}
              aria-expanded={declining}
              aria-controls={noteId}
              data-testid="decline-request"
              className="aria-disabled:opacity-50"
              onMouseDown={(event) => event.preventDefault()}
              onClick={() => {
                if (pressed) return;
                setDeclining((open) => !open);
              }}
            >
              <X className="size-3.5" aria-hidden="true" />
              Decline
            </Button>
          </div>
          <form
            id={noteId}
            hidden={!declining}
            className="mt-2 flex min-w-0 flex-wrap items-center gap-2"
            data-testid="decline-form"
            onSubmit={(event) => {
              event.preventDefault();
              void send("decline");
            }}
          >
            <label htmlFor={`${noteId}-field`} className="sr-only">
              Why decline this request
            </label>
            <Input
              id={`${noteId}-field`}
              value={note}
              maxLength={300}
              placeholder="Why (optional)"
              className="h-8 min-w-0 flex-1 basis-40"
              onChange={(event) => setNote(event.target.value)}
            />
            <Button
              type="submit"
              size="sm"
              variant="outline"
              aria-disabled={pressed}
              data-testid="decline-submit"
              className="aria-disabled:opacity-50"
              onMouseDown={(event) => event.preventDefault()}
            >
              Send decline
            </Button>
          </form>
          <p className="mt-2 text-muted-foreground text-xs">
            Nothing changes until you press Approve: until then the signature, and every shot's
            checks, stay as they are.
          </p>
        </>
      ) : (
        <div className="mt-2 space-y-1" data-testid="request-answer" data-status={shown.status}>
          <Answered request={shown} />
        </div>
      )}

      {failure ? (
        <p role="alert" className="mt-2 text-destructive text-sm" data-testid="request-error">
          {signatureRequestFailure(failure)}
        </p>
      ) : null}
    </div>
  );
}

/** The limit change as one line when only the limit moved, else the old and the new sentence. */
function limitChange(change: SignatureRequestChange): { head: string; from: string; to: string } {
  const prefix = (sentence: string) => sentence.slice(0, Math.max(0, sentence.lastIndexOf(", ")));
  const same =
    change.old_limit_text !== "" &&
    change.new_limit_text !== "" &&
    prefix(change.old_sentence) !== "" &&
    prefix(change.old_sentence) === prefix(change.new_sentence);
  return same
    ? { head: prefix(change.old_sentence), from: change.old_limit_text, to: change.new_limit_text }
    : { head: "", from: change.old_sentence, to: change.new_sentence };
}

function ChangeLines({ change }: { change: SignatureRequestChange }) {
  const where = [change.tier, change.phase].filter(Boolean).join(" · ");
  const moved = change.kind === "replace" ? limitChange(change) : null;
  return (
    <div className="min-w-0 space-y-1">
      <p className="break-words text-sm">
        <span className="font-medium">{KIND_WORDS[change.kind]}</span>
        {where ? <span className="text-muted-foreground"> {where}</span> : null}
      </p>
      {moved ? (
        <p className="break-words text-sm" data-testid="request-limits">
          {moved.head ? <span>{moved.head} </span> : null}
          <span className="text-muted-foreground">{moved.from}</span>
          {" → "}
          <span className="font-medium">{moved.to}</span>
        </p>
      ) : change.kind === "tier" ? (
        <>
          <p className="break-words text-sm text-muted-foreground">{change.old_sentence}</p>
          <p className="break-words text-sm" data-testid="request-tiers">
            {change.old_tier} → {change.new_tier}
          </p>
        </>
      ) : change.kind === "put_back" || change.kind === "put_back_override" ? (
        <>
          <p className="break-words text-sm">{change.old_sentence}</p>
          {change.kind === "put_back_override" && change.new_sentence ? (
            <p className="break-words text-sm text-muted-foreground">
              This version's limit: {change.new_sentence}
            </p>
          ) : null}
          <p className="break-words text-muted-foreground text-xs" data-testid="request-rejected">
            you rejected it {change.rejected_at ? formatTime(change.rejected_at) : ""}
            {change.rejected_reason ? `: “${change.rejected_reason}”` : ""}
          </p>
        </>
      ) : (
        <p className="break-words text-sm">{change.old_sentence}</p>
      )}
      <p className="break-words text-muted-foreground text-sm">
        “<span data-testid="request-change-reason">{change.reason}</span>”
      </p>
      {change.stays_in_force?.map((other) => (
        <p
          key={other.expectation_id}
          className="break-words text-muted-foreground text-sm"
          data-testid="request-stays"
        >
          #{other.expectation_id} “{other.words}” stays in force.
        </p>
      ))}
      {change.preview ? (
        <p className="break-words text-sm" data-testid="request-preview">
          {previewWords(change.kind, change.preview)}
        </p>
      ) : null}
    </div>
  );
}

/**
 * What a change does to the version's shots, said from the numbers: a check that fails on none
 * "passes on all" of them, and a row that is out of force (a put back) has no "now" to report.
 */
export function previewWords(
  kind: SignatureRequestChange["kind"],
  preview: { shots: number; failing_now: number; failing_after: number },
): string {
  const { shots, failing_now: now, failing_after: after } = preview;
  if (shots === 0) return "This version has no shots yet, so there is nothing to count.";
  const head = `On this version's ${shots} ${shots === 1 ? "shot" : "shots"}:`;
  const would = (failing: number) =>
    failing === 0 ? `would pass on all ${shots}` : `would fail on ${failing}`;
  if (kind === "put_back") return `${head} once it is back, this check ${would(after)}.`;
  const today = now === 0 ? `passes on all ${shots} now` : `fails on ${now} now`;
  if (kind === "retire") {
    return `${head} this check ${today}, and nothing is checked once it is retired.`;
  }
  return `${head} this check ${today}, and ${would(after)}.`;
}

function Answered({ request }: { request: SignatureRequest }) {
  const when = request.answered_at ? formatTime(request.answered_at) : "";
  if (request.status === "declined") {
    return (
      <p className="break-words text-sm" data-testid="request-declined">
        Declined {when}
        {request.answer_reason ? `: “${request.answer_reason}”.` : "."} Nothing changed, and the
        agent is told.
      </p>
    );
  }
  if (request.status === "stale") {
    return (
      <p className="break-words text-sm" data-testid="request-stale">
        No longer open. {request.stale_reason} Nothing was changed.
      </p>
    );
  }
  const restorable = request.changes.some((c) => c.kind === "replace" || c.kind === "retire");
  return (
    <>
      <p className="break-words text-sm" data-testid="request-approved">
        Approved {when}.
      </p>
      <ul className="space-y-0.5">
        {request.changes.map((change, index) => (
          // biome-ignore lint/suspicious/noArrayIndexKey: stable, ordered, never reordered
          <li key={index} className="break-words text-sm">
            {approvedLine(change)}
          </li>
        ))}
      </ul>
      {restorable ? (
        <p className="break-words text-muted-foreground text-xs">
          Restore it on the Profiles page, under Rejected.
        </p>
      ) : null}
    </>
  );
}

function approvedLine(change: SignatureRequestChange): string {
  const quoted = (text: string) => `“${text}”`;
  if (change.kind === "replace") {
    const moved = limitChange(change);
    // Quoted either way, like the other rows: the limit alone, or the whole sentence.
    const was = quoted(moved.head ? moved.from : change.old_sentence);
    const now = quoted(moved.head ? moved.to : change.new_sentence);
    return `${was} is rejected with the agent's reason; ${now} is in force.`;
  }
  if (change.kind === "retire") {
    return `${quoted(change.old_sentence)} is rejected with the agent's reason.`;
  }
  if (change.kind === "tier") {
    return `${quoted(change.old_sentence)} is now ${change.new_tier}.`;
  }
  if (change.kind === "put_back") return `${quoted(change.old_sentence)} is back in force.`;
  return `This version's limit for ${quoted(change.old_sentence)} is back in force.`;
}
