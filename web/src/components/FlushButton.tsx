import { useMutation } from "@tanstack/react-query";
import { Droplet } from "lucide-react";
import { useRef } from "react";
import { toast } from "sonner";
import { startFlush } from "@/api/client";
import { useDeviceStatus } from "@/hooks/useDeviceStatus";
import { useSettings } from "@/hooks/useSettings";

/**
 * The top-bar Flush button: the machine's own flush, once, from here.
 *
 * It does what the Flush button on the machine's web UI does: one click runs the
 * flush for the duration set on the machine. There is no hold-to-flush, and nothing
 * is recorded. It exists only while the Writes switch beside it is on, and the
 * server checks the switch again, along with brew mode and nothing running, before
 * anything is sent; its refusal comes back as the toast.
 *
 * Icon-only below `lg`, so the header still fits at phone width and beside the sidebar.
 */
export function FlushButton() {
  const settings = useSettings();
  const device = useDeviceStatus();
  // A second click before the first answer re-renders would send a second flush.
  const inFlight = useRef(false);

  const flush = useMutation({
    mutationFn: startFlush,
    onSuccess: () => toast.success("Flushing, for the time set on the machine."),
    onError: (error: Error) => toast.error(error.message),
    onSettled: () => {
      inFlight.current = false;
    },
  });

  const entry = settings.data?.deviceWritesEnabled;
  const writesOn = entry !== undefined && !entry.secret && entry.value === true;
  if (!writesOn) return null;

  const connected = device.data?.connected ?? false;
  const disabled = !connected || flush.isPending;

  return (
    <button
      type="button"
      aria-label="Flush"
      data-testid="flush-button"
      disabled={disabled}
      title={
        connected
          ? "Run the machine's flush once, for the time set on the machine."
          : "The machine is not connected."
      }
      onClick={() => {
        if (inFlight.current) return;
        inFlight.current = true;
        flush.mutate();
      }}
      className="inline-flex shrink-0 items-center gap-1.5 rounded-full border border-border px-1.5 py-1 font-medium text-xs hover:bg-accent sm:px-2.5 disabled:opacity-60"
    >
      <Droplet className="size-3.5" aria-hidden="true" />
      <span aria-hidden="true" className="max-lg:sr-only">
        Flush
      </span>
    </button>
  );
}
