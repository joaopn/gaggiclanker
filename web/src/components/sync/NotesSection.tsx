import { Send } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import type { PendingNotesData } from "@/api/types";
import { SectionCard } from "@/components/layout/SectionCard";
import { RatingStars } from "@/components/shots/RatingStars";
import { ConfirmStrip } from "@/components/sync/ConfirmStrip";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { usePendingNotes, usePushPendingNotes } from "@/hooks/useDeviceStatus";
import { formatTime } from "@/lib/shots";
import { writeBlocker } from "@/lib/sync";

/**
 * Send notes to the machine: judgements its own notes cards do not have yet.
 *
 * The only way a verdict reaches the machine. Saving a judgement never sends
 * one; a person picks shots here and confirms, and the server refuses a
 * selection that is no longer pending, so what goes out is what was ticked.
 *
 * Nothing is ticked to begin with. "Select all" is one click, and a list that
 * arrived pre-selected would turn this back into the automatic send it
 * replaces with an extra step in front of it.
 *
 * A verdict that came *from* the machine and was never edited is not pending —
 * it is the machine's own words, and sending them back would win every future
 * conflict comparison. That rule is on the server; what shows here is its
 * result, which is why the list can be empty on a page full of rated shots.
 */
export function NotesSection({
  configured,
  connected,
}: {
  configured: boolean;
  connected: boolean;
}) {
  const pending = usePendingNotes();
  const push = usePushPendingNotes();
  const [selected, setSelected] = useState<ReadonlySet<number>>(new Set());
  const [confirming, setConfirming] = useState(false);

  const items = pending.data?.items ?? [];
  // The selection is intersected with the list on every render rather than
  // pruned in an effect: a shot sent from another tab drops out of the list,
  // and a count that still included it would be a number nobody can see.
  const chosen = items.filter((item) => selected.has(item.shot_id)).map((item) => item.shot_id);
  const blocker = writeBlocker({
    configured,
    connected,
    writesEnabled: pending.data?.writes_enabled ?? false,
  });
  const allChosen = items.length > 0 && chosen.length === items.length;

  function toggle(shotId: number) {
    setConfirming(false);
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(shotId)) next.delete(shotId);
      else next.add(shotId);
      return next;
    });
  }

  function toggleAll() {
    setConfirming(false);
    setSelected(allChosen ? new Set() : new Set(items.map((item) => item.shot_id)));
  }

  function send() {
    const shotIds = chosen;
    setConfirming(false);
    push.mutate(shotIds, {
      onSuccess: (accepted) => {
        setSelected(new Set());
        toast.success(
          `Sending ${accepted.pending} judgement${accepted.pending === 1 ? "" : "s"} to the machine`,
        );
      },
      onError: (error: Error) => toast.error(error.message),
    });
  }

  return (
    <SectionCard
      title="Send notes to the machine"
      description="Your verdicts, written to each shot's notes card on the display. Saving a judgement never sends it: only this does, when you confirm."
    >
      {pending.isPending ? (
        <Skeleton className="h-16 w-full" />
      ) : (
        <div className="space-y-3" data-testid="notes-pending">
          <p className="text-sm">
            {items.length === 0
              ? "Every judgement here is already on the machine."
              : `${items.length} judgement${items.length === 1 ? "" : "s"} the machine does not have yet.`}
          </p>
          {items.length > 0 ? (
            <PendingList
              data={pending.data}
              chosen={chosen}
              allChosen={allChosen}
              onToggle={toggle}
              onToggleAll={toggleAll}
            />
          ) : null}
          <p className="text-muted-foreground text-xs">
            Fields sent: {(pending.data?.fields ?? []).join(", ") || "none"}. A note typed on the
            machine more recently than the verdict here is left alone.
          </p>
          {blocker ? (
            <p className="text-muted-foreground text-sm" data-testid="notes-blocked">
              {blocker}
            </p>
          ) : null}
          {confirming ? (
            <ConfirmStrip
              testId="notes-confirm"
              title={`Send ${chosen.length} judgement${chosen.length === 1 ? "" : "s"} to the machine?`}
              confirmLabel={`Send ${chosen.length}`}
              onConfirm={send}
              onCancel={() => setConfirming(false)}
            >
              Each one is written over those fields on that shot's notes card on the display. A card
              edited on the machine since the verdict here was saved is skipped.
            </ConfirmStrip>
          ) : (
            <Button
              size="sm"
              variant="outline"
              onClick={() => setConfirming(true)}
              disabled={blocker !== null || chosen.length === 0 || push.isPending}
            >
              <Send className="size-3.5" aria-hidden="true" />
              Send {chosen.length} to the machine
            </Button>
          )}
        </div>
      )}
    </SectionCard>
  );
}

function PendingList({
  data,
  chosen,
  allChosen,
  onToggle,
  onToggleAll,
}: {
  data: PendingNotesData | undefined;
  chosen: number[];
  allChosen: boolean;
  onToggle: (shotId: number) => void;
  onToggleAll: () => void;
}) {
  const items = data?.items ?? [];
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-left text-sm">
        <thead className="border-border border-b text-muted-foreground text-xs uppercase tracking-wide">
          <tr>
            <th className="py-2 pr-3 font-medium">
              <input
                type="checkbox"
                aria-label="Select every pending judgement"
                checked={allChosen}
                onChange={onToggleAll}
              />
            </th>
            <th className="py-2 pr-4 font-medium">Shot</th>
            <th className="py-2 pr-4 font-medium">Pulled</th>
            <th className="py-2 pr-4 font-medium">Profile</th>
            <th className="py-2 pr-4 font-medium">Rating</th>
            <th className="py-2 font-medium">Notes</th>
          </tr>
        </thead>
        <tbody data-testid="notes-pending-list">
          {items.map((item) => (
            <tr key={item.shot_id} className="border-border border-b last:border-0">
              <td className="py-2 pr-3">
                <input
                  type="checkbox"
                  aria-label={`Select shot ${item.device_id}`}
                  checked={chosen.includes(item.shot_id)}
                  onChange={() => onToggle(item.shot_id)}
                />
              </td>
              <td className="py-2 pr-4 font-mono text-xs">
                <Link to={`/shots/${item.shot_id}`} className="hover:underline">
                  {item.device_id}
                </Link>
              </td>
              <td className="py-2 pr-4">{formatTime(item.started_at)}</td>
              <td className="py-2 pr-4">{item.profile_name || "—"}</td>
              <td className="py-2 pr-4">
                <RatingStars rating={item.rating ?? null} />
              </td>
              <td className="max-w-64 truncate py-2 text-muted-foreground">{item.notes || "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
