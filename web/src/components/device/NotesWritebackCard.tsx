import { Send } from "lucide-react";
import { toast } from "sonner";
import { SectionCard } from "@/components/layout/SectionCard";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { usePendingNotes, usePushPendingNotes } from "@/hooks/useDeviceStatus";

/**
 * Judgements the machine's own notes card does not have yet.
 *
 * One switch gates this and the card says when it is in the way, because
 * "nothing happens when I press the button" is the failure this feature is most
 * likely to produce: `deviceWritesEnabled` is the master switch in front of
 * every write. Saving a judgement never sends it; this button does.
 *
 * A verdict that came *from* the machine and was never edited is not pending —
 * it is the machine's own words, and sending them back would win every future
 * conflict comparison. That rule is on the server; what shows here is its
 * result, which is why the count can be zero on a page full of rated shots.
 */
export function NotesWritebackCard() {
  const pending = usePendingNotes();
  const push = usePushPendingNotes();
  const count = pending.data?.shot_ids.length ?? 0;
  const enabled = Boolean(pending.data?.writes_enabled);

  return (
    <SectionCard
      title="Notes on the machine"
      description="Your verdict, mirrored to the shot's notes card on the display — rating, dose and grind, as the touchscreen shows them."
      actions={
        <>
          <Badge variant={enabled ? "secondary" : "outline"}>
            {enabled ? "write-back on" : "write-back off"}
          </Badge>
          <Button
            variant="outline"
            size="sm"
            onClick={() =>
              push.mutate(undefined, {
                onSuccess: (accepted) =>
                  toast.success(`Pushing ${accepted.pending} judgements to the machine`),
                onError: (error: Error) => toast.error(error.message),
              })
            }
            disabled={!enabled || count === 0 || push.isPending}
          >
            <Send className="size-3.5" aria-hidden="true" />
            Push pending notes
          </Button>
        </>
      }
    >
      {pending.isPending ? (
        <Skeleton className="h-10 w-full" />
      ) : (
        <div className="space-y-2" data-testid="notes-writeback">
          <p className="text-sm">
            {count === 0
              ? "Every judgement here is already on the machine."
              : `${count} judgement${count === 1 ? "" : "s"} the machine does not have yet.`}
          </p>
          {!enabled ? (
            <p className="text-muted-foreground text-sm">
              Device writes are off. Turn on “Device writes enabled” under Settings → Machine.
            </p>
          ) : (
            <p className="text-muted-foreground text-xs">
              Fields sent: {(pending.data?.fields ?? []).join(", ") || "none"}. A note typed on the
              machine more recently than the verdict here is left alone.
            </p>
          )}
        </div>
      )}
    </SectionCard>
  );
}
