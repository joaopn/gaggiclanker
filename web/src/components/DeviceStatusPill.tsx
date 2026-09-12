import { Link } from "react-router-dom";
import type { DeviceIdentity } from "@/api/types";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useLiveStatus } from "@/hooks/useDeviceLive";
import { useDeviceStatus } from "@/hooks/useDeviceStatus";
import { useHealth } from "@/hooks/useHealth";
import { DEVICE_MODES, formatNumber } from "@/lib/shots";
import { cn } from "@/lib/utils";

/**
 * The header pill: is anything working, and is the machine attached.
 *
 * The backend comes first because nothing else can be known without it — an
 * unreachable backend makes the device question unanswerable rather than
 * negative. After that there are three machine states and they are genuinely
 * different: no machine configured is a setup step and not a fault; configured
 * but disconnected is something to go and look at; connected names the board.
 *
 * Deliberately small: it says whether to go to the device page, and links
 * there. The boiler temperature comes off the live stream rather than the
 * status poll, so the one number that moves is the one that is current.
 */
export function DeviceStatusPill() {
  const health = useHealth();
  const device = useDeviceStatus();
  const live = useLiveStatus();

  const backendOk = !health.isPending && !health.isError && health.data?.status === "ok";
  const identity = device.data?.identity as DeviceIdentity | null | undefined;
  const hardware = identity?.hardware ?? undefined;

  let state: "pending" | "good" | "bad";
  let label: string;
  let detail: string;

  if (health.isPending) {
    state = "pending";
    label = "Checking";
    detail = "Contacting the backend";
  } else if (!backendOk) {
    state = "bad";
    label = "Offline";
    detail = `Backend unreachable: ${health.error?.message ?? "unknown error"}`;
  } else if (!device.data?.configured) {
    state = "good";
    label = "Online";
    detail = `gaggiclanker ${health.data?.version} - no machine configured`;
  } else if (device.data.connected) {
    state = "good";
    label = hardware ?? "Machine online";
    const warnings = (live.status?.warn ?? []).filter((warning) => warning.a);
    if (warnings.length > 0) state = "bad";
    detail =
      `${hardware ?? "GaggiMate"} at ${device.data.host} - display ${
        identity?.displayVersion ?? "unknown"
      } - ${DEVICE_MODES[live.status?.m ?? 0] ?? "unknown mode"}` +
      (live.status?.ct == null ? "" : ` at ${formatNumber(live.status.ct, 1, "°C")}`) +
      (warnings.length > 0 ? ` - ${warnings.map((w) => w.k).join(", ")}` : "");
  } else {
    state = "bad";
    label = "Machine offline";
    detail = `No connection to ${device.data.host}. The archive still works; nothing is syncing.`;
  }

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Link
          to="/device"
          data-testid="device-status-pill"
          data-state={state}
          className={cn(
            "inline-flex items-center gap-2 rounded-full border border-border px-2.5 py-1 font-medium text-xs",
            state === "good" && "text-status-good-text",
            state === "bad" && "text-status-bad-text",
            state === "pending" && "text-muted-foreground",
          )}
        >
          <span
            aria-hidden="true"
            className={cn(
              "size-2 rounded-full",
              state === "good" && "bg-status-good",
              state === "bad" && "bg-status-bad",
              state === "pending" && "animate-pulse bg-muted-foreground",
            )}
          />
          {label}
          {live.status?.ct == null ? null : (
            <span className="hidden font-normal text-muted-foreground tabular-nums sm:inline">
              {formatNumber(live.status.ct, 0, "°C")}
            </span>
          )}
        </Link>
      </TooltipTrigger>
      <TooltipContent>{detail}</TooltipContent>
    </Tooltip>
  );
}
