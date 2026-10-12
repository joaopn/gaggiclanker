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
 * Four states. While connected it also shows the boiler's temperature, read
 * from the same fifteen-second status poll that says whether the machine is
 * there. It once carried the temperature off a 2 Hz stream, which made the
 * one element on every page the one that re-rendered twice a second; the poll
 * keeps it to one render per read, at the cost of a reading up to fifteen
 * seconds old. A disconnected machine shows no temperature, since the last
 * one the box heard is no longer the machine's.
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
  let temperature: number | null = null;

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
    temperature = boilerTemperature(device.data.last_status?.ct);
  } else {
    state = "bad";
    label = "Machine offline";
    detail = `No connection to ${device.data.host}. The archive still works; a sync cannot.`;
  }

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Link
          to="/device"
          data-testid="device-status-pill"
          data-state={state}
          className={cn(
            "inline-flex items-center gap-1 rounded-full border border-border px-1.5 py-1 font-medium text-xs sm:gap-2 sm:px-2.5",
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
          {/* The dot alone below xl; the words stay in the link's name and tooltip. The
              temperature stays visible at every width, as "93°" below sm: it is the one
              thing here a person glances at, and with the board's name beside it the bar
              no longer fits beside the sidebar at 1024 px with sign-in on. */}
          <span className="max-xl:sr-only">{label}</span>
          {temperature !== null ? (
            <span data-testid="device-temperature" className="tabular-nums">
              <span className="sr-only">boiler at </span>
              {temperature}
              <span className="max-sm:sr-only"> °C</span>
              <span aria-hidden="true" className="sm:hidden">
                °
              </span>
            </span>
          ) : null}
          {/* The pill's text is the machine's state, not a destination, so the
              link says where it goes out loud as well as in the tooltip. */}
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

/**
 * The boiler's current temperature (`evt:status` `ct`) in whole degrees, or null when
 * the machine has not reported one. Whole degrees, as the firmware web UI's overview
 * chart rounds them: a tenth means nothing in a reading up to a poll old.
 */
export function boilerTemperature(ct: unknown): number | null {
  if (typeof ct !== "number" || !Number.isFinite(ct)) return null;
  return Math.round(ct);
}
