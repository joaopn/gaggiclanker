import { useMutation, useQueryClient } from "@tanstack/react-query";
import { PenLine, PenOff } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { patchSettings } from "@/api/client";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { useSettings } from "@/hooks/useSettings";
import { invalidateSettings } from "@/lib/invalidate";
import { queryKeys } from "@/lib/queryKeys";
import { cn } from "@/lib/utils";

/**
 * What turning the switch on allows, in the one sentence the confirmation shows.
 *
 * Kept as plain a statement of today's behaviour as it can be: the switch gates
 * every write to the machine, and the only writes that exist are a person's
 * profile pushes and rollbacks. Exported so a test pins the wording.
 */
export const WRITES_ON_SENTENCE =
  "Turn on writes? For now that only lets you push a profile to the machine, or roll one back, from the Profiles page.";

/**
 * The top-bar switch for writing to the machine.
 *
 * Next to the machine's status because it belongs to it: the pill says whether
 * the box can reach the machine and this says whether the box may change
 * anything on it. Off is the default and is immediate, since the person turning
 * it off is usually the one who just saw something they did not like. On asks
 * first, in a small panel under the switch, because it is the one control in
 * the header that can change somebody's espresso machine.
 *
 * It reads and writes the `deviceWritesEnabled` setting through the same
 * `PATCH /api/settings` the settings pages use. A refused change (a 401 with
 * sign-in on, an unreachable backend) leaves the setting as it was and says so
 * in the same panel, rather than a toast that is gone before it is read.
 *
 * Width: from `lg` up it reads "Writes on" / "Writes off"; from `sm` to `lg`
 * "on" / "off"; below `sm` it is the icon alone (a pen when on, a crossed-out
 * pen when off, plus the colour and `aria-checked`), because at 375 px the
 * header has no room for text beside the status pill and the theme toggle.
 * Its panel is a card under the switch from `sm` up and spans the viewport
 * under it below `sm`.
 */
export function DeviceWritesSwitch() {
  const settings = useSettings();
  const queryClient = useQueryClient();
  const [panel, setPanel] = useState<"confirm" | "error" | null>(null);
  const closeRef = useRef<HTMLButtonElement>(null);

  // The confirmation's buttons are removed when a change fails, which would
  // drop focus to <body>; the error's Close button takes it instead.
  useEffect(() => {
    if (panel === "error") closeRef.current?.focus();
  }, [panel]);

  const entry = settings.data?.deviceWritesEnabled;
  const known = entry !== undefined;
  const on = entry !== undefined && !entry.secret && entry.value === true;

  const change = useMutation({
    mutationFn: (next: boolean) => patchSettings({ deviceWritesEnabled: next }),
    onSuccess: (data) => {
      queryClient.setQueryData(queryKeys.settings.current(), data);
      setPanel(null);
    },
    onError: () => setPanel("error"),
    // Every reader of the switch, success or not: the Profiles page's banner
    // reads the write audit's `enabled`, the settings pages read the registry,
    // and after a failure the server is the authority on what the switch is.
    onSettled: () => {
      void invalidateSettings(queryClient);
      void queryClient.invalidateQueries({ queryKey: queryKeys.device.writes() });
    },
  });

  function onClick() {
    if (!known || change.isPending) return;
    if (on) {
      change.mutate(false);
    } else {
      change.reset();
      setPanel("confirm");
    }
  }

  const state = !known ? "unknown" : on ? "on" : "off";
  const word = !known ? "…" : on ? "on" : "off";
  const Icon = on ? PenLine : PenOff;

  // `max-sm:static` on the popover wrapper: below `sm` the panel spans the
  // viewport, so it is positioned against the sticky header (the nearest
  // positioned ancestor) instead of the wrapper. Nothing about it depends on
  // the header's backdrop filter making it a containing block for fixed
  // elements.
  return (
    <Popover
      className="max-sm:static"
      open={panel !== null}
      onOpenChange={(open) => {
        if (!open) setPanel(null);
      }}
    >
      <PopoverTrigger asChild popupAttributes={false}>
        <button
          type="button"
          role="switch"
          aria-checked={on}
          aria-label="Machine writes"
          data-testid="device-writes-switch"
          data-state={state}
          disabled={!known || change.isPending}
          title={
            on
              ? "Writes to the machine are on. Click to turn them off."
              : "Writes to the machine are off. Click to turn them on."
          }
          onClick={(event) => {
            // The popover is driven by this component's own state, not by the
            // trigger's toggle: a click on an "on" switch opens nothing.
            event.preventDefault();
            onClick();
          }}
          className={cn(
            "inline-flex shrink-0 items-center gap-1.5 rounded-full border px-2.5 py-1 font-medium text-xs disabled:opacity-60",
            on
              ? "border-status-warn/50 bg-status-warn/10 text-status-warn-text"
              : "border-border text-muted-foreground",
          )}
        >
          <Icon className="size-3.5" aria-hidden="true" />
          <span aria-hidden="true" className="max-sm:sr-only">
            <span className="hidden lg:inline">Writes </span>
            {word}
          </span>
        </button>
      </PopoverTrigger>
      <PopoverContent
        align="end"
        aria-label="Writes to the machine"
        className="max-sm:inset-x-4 max-sm:w-auto sm:w-80"
      >
        {panel === "error" ? (
          <div className="space-y-3" role="alert" data-testid="device-writes-error">
            <p className="text-sm text-status-bad-text">
              Could not change the setting
              {change.error ? `: ${change.error.message}` : "."} Writes are still{" "}
              {on ? "on" : "off"}.
            </p>
            <div className="flex justify-end">
              <Button ref={closeRef} size="sm" variant="outline" onClick={() => setPanel(null)}>
                Close
              </Button>
            </div>
          </div>
        ) : (
          <div className="space-y-3">
            <p className="text-sm">{WRITES_ON_SENTENCE}</p>
            <div className="flex justify-end gap-2">
              <Button size="sm" variant="outline" onClick={() => setPanel(null)}>
                Cancel
              </Button>
              <Button size="sm" disabled={change.isPending} onClick={() => change.mutate(true)}>
                {change.isPending ? "Turning on…" : "Turn on"}
              </Button>
            </div>
          </div>
        )}
      </PopoverContent>
    </Popover>
  );
}
