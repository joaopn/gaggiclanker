import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useHealth } from "@/hooks/useHealth";
import { cn } from "@/lib/utils";

/**
 * The header pill.
 *
 * Today it reports the backend (`/health`), which is the only liveness fact
 * that exists before the device client. When that lands it grows a second dot
 * for the machine itself — the shape is already right, so that is an edit here
 * and nothing else.
 */
export function DeviceStatusPill() {
  const { data, isPending, isError, error } = useHealth();

  const state = isPending ? "pending" : isError || data?.status !== "ok" ? "bad" : "good";
  const label = state === "pending" ? "Checking" : state === "good" ? "Online" : "Offline";
  const detail =
    state === "pending"
      ? "Contacting the backend"
      : state === "good"
        ? `gaggiclanker ${data?.version} - database ${data?.database}`
        : `Backend unreachable: ${error?.message ?? "unknown error"}`;

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span
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
        </span>
      </TooltipTrigger>
      <TooltipContent>{detail}</TooltipContent>
    </Tooltip>
  );
}
