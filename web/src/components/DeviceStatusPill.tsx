import { Link } from "react-router-dom";
import type { DeviceIdentity } from "@/api/types";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useDeviceStatus } from "@/hooks/useDeviceStatus";
import { useHealth } from "@/hooks/useHealth";
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
 * Four states and nothing else. It used to carry the boiler temperature and
 * the firmware's warnings off a 2 Hz stream, which made the one element on
 * every page the one element that re-rendered twice a second — for numbers the
 * machine's own display is already showing to whoever is standing at it. What
 * is left is the question this box can answer better than the machine can: can
 * it reach it, which is what decides whether a pull will work.
 */
export function DeviceStatusPill() {
  const health = useHealth();
  const device = useDeviceStatus();

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
    detail = `${hardware ?? "GaggiMate"} at ${device.data.host} - display ${
      identity?.displayVersion ?? "unknown"
    }`;
  } else {
    state = "bad";
    label = "Machine offline";
    detail = `No connection to ${device.data.host}. The archive still works; a pull cannot.`;
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
          {/* The device page is not in the sidebar — this pill is the way to
              it — so the link says so out loud as well as in the tooltip. */}
          <span className="sr-only">Open the device page</span>
        </Link>
      </TooltipTrigger>
      <TooltipContent>
        <p>{detail}</p>
        <p className="opacity-80">Open the device page</p>
      </TooltipContent>
    </Tooltip>
  );
}
