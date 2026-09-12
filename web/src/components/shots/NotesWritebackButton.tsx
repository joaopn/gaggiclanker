import { Send } from "lucide-react";
import { toast } from "sonner";
import type { ShotJudgement } from "@/api/types";
import { Button } from "@/components/ui/button";
import { useWriteBackNotes } from "@/hooks/useDeviceStatus";

/**
 * "Sync notes to machine" — one shot's verdict onto the display's notes card.
 *
 * Rendered whatever the settings say, and it is the *result* that explains
 * itself: a refusal comes back from the server as a 200 with a sentence
 * ("write-back is off", "this verdict came from the machine"), which is shown
 * as an ordinary toast rather than as an error. Hiding the button when the
 * feature is off would leave no way to find out that it exists, and disabling it
 * would need this component to know the two switches — a third copy of a rule
 * that already lives on the server.
 *
 * Absent entirely when there is no verdict to send: an empty notes card is not
 * worth a frame to a machine with 300 KB of heap.
 */
export function NotesWritebackButton({
  shotId,
  judgement,
}: {
  shotId: number;
  judgement: ShotJudgement | null | undefined;
}) {
  const writeBack = useWriteBackNotes(String(shotId));
  if (!judgement) return null;

  return (
    <Button
      variant="outline"
      size="sm"
      disabled={writeBack.isPending}
      onClick={() =>
        writeBack.mutate(undefined, {
          onSuccess: (result) =>
            result.written
              ? toast.success(`Sent to the machine's notes for shot ${result.device_id}`)
              : toast.info(result.reason ?? "Nothing was sent to the machine"),
          onError: (error: Error) => toast.error(error.message),
        })
      }
    >
      <Send className="size-3.5" aria-hidden="true" />
      Sync notes to machine
    </Button>
  );
}
