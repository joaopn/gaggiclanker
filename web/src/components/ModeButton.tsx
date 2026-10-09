import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Coffee, Power } from "lucide-react";
import { useRef } from "react";
import { toast } from "sonner";
import { changeMode } from "@/api/client";
import { useDeviceStatus } from "@/hooks/useDeviceStatus";
import { useSettings } from "@/hooks/useSettings";
import { queryKeys } from "@/lib/queryKeys";

/** `evt:status` `m` for brew mode; every other value (standby, steam, water, grind) offers Brew. */
const MODE_BREW = 1;

/**
 * The top-bar mode button: "Switch to Standby" while the machine is in brew mode,
 * "Switch to Brew" in any other mode.
 *
 * It does what the mode buttons on the machine's web UI do. Like Flush, it exists only
 * while the Writes switch is on, and the server checks the switch again, along with
 * nothing running and the machine ready, before anything is sent; its refusal comes
 * back as the toast. The server answers once the machine reports the new mode, and the
 * device status is read again so the label follows.
 *
 * The mode comes from the device status poll, so a change made on the machine itself
 * shows here within its interval. Icon-only below `lg`, so the header still fits at
 * phone width and beside the sidebar.
 */
export function ModeButton() {
  const settings = useSettings();
  const device = useDeviceStatus();
  const queryClient = useQueryClient();
  // A second click before the first answer re-renders would send a second switch.
  const inFlight = useRef(false);

  const switchMode = useMutation({
    mutationFn: (mode: "brew" | "standby") => changeMode(mode),
    onSuccess: (data) =>
      toast.success(
        data.mode === "brew" ? "The machine is in brew mode." : "The machine is in standby.",
      ),
    onError: (error: Error) => toast.error(error.message),
    onSettled: () => {
      inFlight.current = false;
      void queryClient.invalidateQueries({ queryKey: queryKeys.device.status() });
    },
  });

  const entry = settings.data?.deviceWritesEnabled;
  const writesOn = entry !== undefined && !entry.secret && entry.value === true;
  if (!writesOn) return null;

  const connected = device.data?.connected ?? false;
  const reported = device.data?.last_status?.m;
  const mode = typeof reported === "number" ? reported : null;
  const inBrew = mode === MODE_BREW;
  const label = inBrew ? "Switch to Standby" : "Switch to Brew";
  const Icon = inBrew ? Power : Coffee;
  const disabled = !connected || mode === null || switchMode.isPending;

  return (
    <button
      type="button"
      aria-label={label}
      data-testid="mode-button"
      disabled={disabled}
      title={
        !connected
          ? "The machine is not connected."
          : mode === null
            ? "The machine has not said which mode it is in yet."
            : inBrew
              ? "Put the machine in standby."
              : "Put the machine in brew mode."
      }
      onClick={() => {
        if (inFlight.current) return;
        inFlight.current = true;
        switchMode.mutate(inBrew ? "standby" : "brew");
      }}
      className="inline-flex shrink-0 items-center gap-1.5 rounded-full border border-border px-1.5 py-1 font-medium text-xs hover:bg-accent sm:px-2.5 disabled:opacity-60"
    >
      <Icon className="size-3.5" aria-hidden="true" />
      <span aria-hidden="true" className="max-lg:sr-only">
        {label}
      </span>
    </button>
  );
}
