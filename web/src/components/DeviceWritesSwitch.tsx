import { useMutation, useQueryClient } from "@tanstack/react-query";
import { PenLine, PenOff } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { patchSettings } from "@/api/client";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { useProfileBoard } from "@/hooks/useBoard";
import { useSettings } from "@/hooks/useSettings";
import { previewCounts, previewLine } from "@/lib/board";
import { invalidateBoardWrites, invalidateSettings } from "@/lib/invalidate";
import { queryKeys } from "@/lib/queryKeys";
import { cn } from "@/lib/utils";

/**
 * What turning the switch on allows, in the one sentence the confirmation shows.
 *
 * As plain a statement of what a sync does with the switch on as it can be: it makes the
 * machine hold exactly the profiles that are on in the Profiles list. It names the two things
 * a person turning this on is asking about: profiles that are off are removed, the firmware's
 * own included, and a profile edited on the machine is never overwritten without asking.
 * Exported so a test pins the wording.
 */
export const WRITES_ON_SENTENCE =
  "Turn on writes? From then on every sync makes the machine hold exactly the profiles that are on in the Profiles list: it puts back the ones it lacks, replaces a profile with its active version, sets the stars, and removes the profiles that are switched off, the machine's own included. A profile that was edited on the machine is left alone and shown as a conflict for you to decide.";

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
    // Every reader of the switch, success or not: the settings pages read the
    // registry, the profile list and its preview carry `writes_enabled` and what the next
    // sync would do, the Device page and the write audit read the rest, and after a
    // failure the server is the authority on what the switch is.
    onSettled: () => {
      void invalidateSettings(queryClient);
      void invalidateBoardWrites(queryClient);
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
        // Never taller than the screen under the header: the preview's list scrolls
        // inside the panel instead of pushing the buttons off it.
        className="max-h-[calc(100dvh-5rem)] overflow-y-auto max-sm:inset-x-4 max-sm:w-auto sm:w-80"
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
            <WritesPreview />
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

/**
 * What the next sync would do, read from the machine now, under the confirmation.
 *
 * Asked for only while the confirmation is on screen (it is the one read in the
 * app that goes to the machine for a person's answer), and always worded from the
 * server's own plan: before the board has been adopted the first sync writes nothing
 * at all, so that is what it says; after, the counts and the list. A machine that
 * cannot be read says so and falls back to what the last sync saw.
 */
function WritesPreview() {
  const board = useProfileBoard({ live: true });

  if (board.isPending) {
    return (
      <p className="text-muted-foreground text-xs" data-testid="writes-preview-loading">
        Reading the machine to see what the next sync would do…
      </p>
    );
  }
  if (board.isError || !board.data) {
    return (
      <p className="text-muted-foreground text-xs" data-testid="writes-preview">
        The profile list could not be read just now, so what the next sync would do is not shown.
      </p>
    );
  }
  const view = board.data;
  const actions = view.actions ?? [];
  const counts = previewCounts(view);
  const conflicts = (view.reports ?? []).filter((r) => r.reason === "conflict").length;
  const fromMachine = view.machine_source === "machine";

  return (
    <div className="space-y-2 text-xs" data-testid="writes-preview">
      {!fromMachine ? (
        <p className="text-status-warn-text" data-testid="writes-preview-stale">
          {view.machine_source === "mirror"
            ? "The machine could not be read just now, so this is from the last sync."
            : "The machine could not be read just now and nothing is known of its profiles yet."}
        </p>
      ) : null}
      {conflicts > 0 ? (
        <p className="text-status-warn-text" data-testid="writes-preview-conflicts">
          {conflicts} {conflicts === 1 ? "profile is" : "profiles are"} in conflict with the machine
          and will be left alone until you choose a side on the Profiles page.
        </p>
      ) : null}
      {!view.adopted ? (
        <p data-testid="writes-preview-first">
          The first sync takes the machine's profiles into the list and writes nothing.
          {counts.adopt > 0
            ? ` ${counts.adopt} ${counts.adopt === 1 ? "profile is" : "profiles are"} on the machine to take.`
            : ""}
        </p>
      ) : view.paused ? (
        <p data-testid="writes-preview-paused">
          The machine looks reset, so syncs write nothing until you resume them on the Profiles
          page.
        </p>
      ) : actions.length === 0 && !fromMachine ? (
        <p data-testid="writes-preview-unknown">
          Without a read of the machine there is no telling what the next sync would do.
        </p>
      ) : actions.length === 0 ? (
        <p data-testid="writes-preview-none">
          The machine already matches the list: the next sync would change nothing.
        </p>
      ) : (
        <>
          <p data-testid="writes-preview-counts">
            The next sync would{" "}
            {[
              counts.push > 0 &&
                `put ${counts.push} ${counts.push === 1 ? "profile" : "profiles"} on the machine`,
              counts.remove > 0 &&
                `remove ${counts.remove} ${counts.remove === 1 ? "profile" : "profiles"} from it`,
              counts.homeScreen > 0 &&
                `change ${counts.homeScreen} ${counts.homeScreen === 1 ? "star" : "stars"}`,
            ]
              .filter(Boolean)
              .join(", ") || "leave everything as it is"}
            {counts.leave > 0 ? `, and leave ${counts.leave} on the machine` : ""}.
          </p>
          <ul
            className="max-h-32 list-disc space-y-0.5 overflow-y-auto pl-5 text-muted-foreground"
            data-testid="writes-preview-list"
          >
            {actions.map((action) => (
              <li
                key={`${action.kind}-${action.row_id ?? "x"}-${action.device_id ?? action.label}`}
                className="break-words"
              >
                {previewLine(action, {
                  rowIsOff:
                    action.row_id != null &&
                    (view.rows ?? []).some((r) => r.row.id === action.row_id && !r.on_machine),
                })}
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}
