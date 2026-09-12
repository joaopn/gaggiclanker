import { AlertTriangle, Check, Sparkles, Trash2, Undo2, Upload } from "lucide-react";
import { useId, useState } from "react";
import type { ProfileDraft, StopConditionChange } from "@/api/types";
import { clampChangesOf, stopConditionChangesOf } from "@/api/types";
import { ProfileDiff } from "@/components/drafts/ProfileDiff";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import {
  useApproveDraft,
  useDiscardDraft,
  useProfileDraft,
  usePushDraft,
  useRefineDraft,
  useRollbackDraft,
} from "@/hooks/useDrafts";
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

export function DraftCard({ draft }: { draft: ProfileDraft }) {
  const detail = useProfileDraft(draft.id);
  const approve = useApproveDraft();
  const push = usePushDraft();
  const rollback = useRollbackDraft();
  const discard = useDiscardDraft();
  const refine = useRefineDraft();

  const [acknowledged, setAcknowledged] = useState(false);
  const [allowStale, setAllowStale] = useState(false);
  const [notes, setNotes] = useState("");
  const [refining, setRefining] = useState(false);
  const acknowledgeId = useId();
  const staleId = useId();
  const notesId = useId();

  const stopChanges = stopConditionChangesOf(draft);
  const clamps = clampChangesOf(draft);
  const badge = STATUS_BADGE[draft.status] ?? { label: draft.status, variant: "outline" as const };
  const busy =
    approve.isPending ||
    push.isPending ||
    rollback.isPending ||
    discard.isPending ||
    refine.isPending;
  const needsAcknowledgement = stopChanges.length > 0 && !draft.acknowledged_stop_changes;

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
            {draft.source_analysis_id ? ` · analysis #${draft.source_analysis_id}` : ""}
          </p>
        </div>
        <Badge variant={badge.variant}>{badge.label}</Badge>
      </div>

      {draft.change_summary ? <p className="mt-2 text-sm">{draft.change_summary}</p> : null}

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
            diff above is against a version the machine no longer holds. Pushing anyway proposes
            undoing whatever was changed there. Drafting again from the current profile is usually
            what you want.
          </p>
          {draft.status === "approved" ? (
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
              ? ` It is on the display as ${draft.pushed_device_profile_id}; rolling back deletes that copy.`
              : " Its copy has already been removed from the display."}
          </p>
        </div>
      ) : null}

      {draft.status === "pushed" ? (
        <p className="mt-3 text-muted-foreground text-xs" data-testid="draft-pushed">
          On the machine as <span className="font-mono">{draft.pushed_device_profile_id}</span>. It
          was not selected — the machine is still brewing with whatever it was brewing with.
        </p>
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

        {draft.status === "approved" ? (
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
        {(draft.status === "failed" || draft.status === "pushed") &&
        draft.pushed_device_profile_id ? (
          <Button
            size="sm"
            variant="destructive"
            disabled={busy}
            data-testid="rollback-draft"
            onClick={() => rollback.mutate(draft.id)}
          >
            <Undo2 className="size-3.5" aria-hidden="true" />
            Delete it from the machine
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
