import { ChevronRight } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import type { BoardProposal, BoardRowView, BoardView } from "@/api/types";
import { NameConflictActions } from "@/components/profiles/NameConflictActions";
import { PinnedPanel } from "@/components/profiles/PinnedPanel";
import { ProfileDropdown } from "@/components/profiles/ProfileDropdown";
import { RenameProfile } from "@/components/profiles/RenameProfile";
import { StateLine } from "@/components/profiles/StateLine";
import { ConfirmStrip } from "@/components/sync/ConfirmStrip";
import { Badge } from "@/components/ui/badge";
import { Switch } from "@/components/ui/switch";
import { useSetOnMachine, useSetStarred } from "@/hooks/useBoard";
import { useSingleFlight } from "@/hooks/useSingleFlight";
import { nameConflictOf, rowStateOf, selectedSuccessorSentence } from "@/lib/board";
import { profileHeadline } from "@/lib/profileInfo";
import { cn } from "@/lib/utils";

type Json = Record<string, unknown>;

/**
 * One profile of the list.
 *
 * The left side opens the row's dropdown (a button, so a keyboard gets it); the two switches
 * sit beside it and wrap under it at phone width. **On the machine** is what a sync does: it
 * puts the profile there or removes it. **Starred** is the machine's home-screen carousel and
 * is kept while the profile is off, but applies only while it is on, so it is disabled with
 * the reason. Switching off a profile a Set brews, or the one the machine has selected, asks
 * first. Nothing here writes to the machine: the next sync does.
 */
export function ProfileRow({
  view,
  entry,
  open,
  onToggle,
  proposals,
}: {
  view: BoardView;
  entry: BoardRowView;
  open: boolean;
  onToggle: () => void;
  proposals: BoardProposal[];
}) {
  const { row } = entry;
  const onMachine = useSetOnMachine();
  const starred = useSetStarred();
  const [confirming, setConfirming] = useState(false);
  // The Name field, opened by the open profile's Rename or by a name conflict's "Rename to …".
  const [renaming, setRenaming] = useState(false);
  const state = rowStateOf(view, entry);
  const nameConflict = nameConflictOf(entry);
  const set = entry.set ?? null;
  const selected = entry.machine.selected === true;
  const pin = entry.pin ?? null;
  const waiting = proposals.length > 0 ? proposals.length : (entry.proposed_versions ?? 0);
  const busy = onMachine.isPending || starred.isPending;
  const once = useSingleFlight();
  const onceStar = useSingleFlight();

  const send = (next: boolean) =>
    once((release) => onMachine.mutate({ rowId: row.id, on: next }, { onSettled: release }));
  const turn = (next: boolean) => {
    if (!next && (set !== null || selected)) {
      setConfirming(true);
      return;
    }
    send(next);
  };

  const rename = (
    <RenameProfile
      rowId={row.id}
      name={row.label}
      editing={renaming}
      onEditingChange={setRenaming}
    />
  );

  return (
    <li
      id={`profile-${row.id}`}
      className="scroll-mt-20 rounded-lg border border-border"
      data-testid="profile-row"
      data-on={entry.on_machine ? "yes" : "no"}
      data-open={open ? "yes" : "no"}
    >
      <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2 p-3">
        <button
          type="button"
          className="flex min-w-0 flex-1 basis-56 items-start gap-2 text-left"
          data-testid="profile-toggle"
          onClick={onToggle}
        >
          <ChevronRight
            className={cn("mt-0.5 size-4 shrink-0 transition-transform", open ? "rotate-90" : "")}
            aria-hidden="true"
          />
          <span className="min-w-0 flex-1">
            <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
              <span className="min-w-0 break-words font-medium text-sm">{row.label}</span>
              {entry.in_conflict ? <Badge variant="destructive">Conflict</Badge> : null}
              {waiting > 0 ? (
                <Badge variant="secondary" data-testid="proposed-badge">
                  {waiting > 1 ? `Proposed (${waiting})` : "Proposed"}
                </Badge>
              ) : null}
              {selected ? <Badge variant="outline">Selected</Badge> : null}
            </span>
            <span className="block break-words text-muted-foreground text-xs">
              {profileHeadline(entry.active_version.profile as Json)}
              {entry.active_version.shots_brewed > 0
                ? ` · ${entry.active_version.shots_brewed} ${entry.active_version.shots_brewed === 1 ? "shot" : "shots"}`
                : ""}
            </span>
            {pin ? null : (
              <span className="block text-muted-foreground text-xs" data-testid="row-set">
                {set ? `Set: ${set.name}` : "No Set"}
              </span>
            )}
            <StateLine state={state} />
          </span>
        </button>

        <div
          className={cn(
            "flex items-center",
            // The badge carries a Set's name, which can be long: that group wraps and shrinks.
            pin ? "min-w-0 max-w-full flex-wrap gap-x-4 gap-y-2" : "shrink-0 gap-4",
          )}
        >
          {/* A pinned copy is on the machine for as long as it is pinned and is taken off only by
              unpinning, so it has no switch; the badge says whose it is and goes to that Set. */}
          {pin ? (
            <Link
              to={`/sets/${pin.set_id}`}
              data-testid="pinned-badge"
              className="inline-flex min-w-0 max-w-full items-center gap-1 rounded-md border border-border px-2 py-0.5 text-xs underline-offset-2 hover:underline"
            >
              <span aria-hidden="true">📌</span>
              <span className="min-w-0 break-words">
                Pinned: {pin.set_name} {pin.version_label}
              </span>
            </Link>
          ) : (
            <div className="flex items-center gap-2 text-sm">
              <Switch
                checked={entry.on_machine}
                disabled={busy}
                label={`${row.label} on the machine`}
                data-testid="on-machine-switch"
                onCheckedChange={turn}
              />
              <span aria-hidden="true">On the machine</span>
            </div>
          )}
          <div className="flex items-center gap-2 text-sm">
            <Switch
              checked={entry.starred}
              disabled={busy || !entry.on_machine}
              label={`${row.label} starred`}
              title={
                entry.on_machine
                  ? undefined
                  : "Starred applies only while the profile is on the machine. Your choice is kept."
              }
              data-testid="starred-switch"
              onCheckedChange={(next) =>
                onceStar((release) =>
                  starred.mutate({ rowId: row.id, starred: next }, { onSettled: release }),
                )
              }
            />
            <span aria-hidden="true" className={entry.on_machine ? "" : "text-muted-foreground"}>
              Starred
            </span>
          </div>
        </div>
      </div>

      {nameConflict ? (
        <div className="px-3 pb-3 pl-9">
          <NameConflictActions
            entry={entry}
            conflict={nameConflict}
            onRenameTo={() => {
              setRenaming(true);
              if (!open) onToggle();
            }}
          />
        </div>
      ) : null}

      {confirming ? (
        <div className="px-3 pb-3">
          <ConfirmStrip
            title={`Switch ${row.label} off the machine?`}
            confirmLabel="Switch off"
            confirmVariant="default"
            testId="switch-off-confirm"
            focusOnOpen
            onCancel={() => setConfirming(false)}
            onConfirm={() => {
              setConfirming(false);
              send(false);
            }}
          >
            {set !== null ? (
              <p data-testid="switch-off-sets">
                {set.name} brews this profile. The next sync takes it off the machine, and it can no
                longer brew it there.
              </p>
            ) : null}
            {selected ? (
              <p data-testid="switch-off-selected">{selectedSuccessorSentence()}</p>
            ) : null}
          </ConfirmStrip>
        </div>
      ) : null}

      {open ? (
        pin ? (
          <PinnedPanel entry={entry} pin={pin} rename={rename} />
        ) : (
          <ProfileDropdown entry={entry} rename={rename} />
        )
      ) : null}
    </li>
  );
}
