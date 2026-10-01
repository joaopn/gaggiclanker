import { AlertTriangle, Check, Clock, Trash2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { BoardRowView, BoardView, ProfileDraft } from "@/api/types";
import { ConfirmStrip } from "@/components/sync/ConfirmStrip";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useDeleteBoardRow, useSetHomeScreen } from "@/hooks/useBoard";
import { deletedStateOf, ownerOf, type RowState, rowStateOf } from "@/lib/board";
import { cn } from "@/lib/utils";

/**
 * The profiles the app means the machine to hold: one card per board row.
 *
 * Cards rather than a table so each one reads at a phone's width: the name and who it
 * belongs to on the first line, where it stands on the machine under it, and the two
 * controls (the home-screen tick and Delete) wrapping underneath. Nothing here writes to
 * the machine: a tick or a delete edits the board, and the next sync makes the machine
 * match it.
 */
export function BoardList({
  view,
  versionName,
  draftOf,
}: {
  view: BoardView;
  /** The draft a row is waiting on, to name its Set. */
  draftOf: (draftId: number) => ProfileDraft | undefined;
  /** The short name of a version (its hash), or `null` when the page does not hold it. */
  versionName: (versionId: number) => string | null;
}) {
  const rows = view.rows ?? [];
  const removals = (view.pending_removals ?? [])
    .map((row) => ({ row, state: deletedStateOf(view, row) }))
    .filter((entry): entry is { row: typeof entry.row; state: RowState } => entry.state !== null);

  if (rows.length === 0 && removals.length === 0) {
    return (
      <p className="text-muted-foreground text-sm" data-testid="board-empty">
        Nothing is on the board yet. Put an approved draft on it below.
      </p>
    );
  }
  return (
    <div className="space-y-3">
      <ul className="space-y-3" data-testid="board-list">
        {rows.map((entry) => (
          <BoardRowCard
            key={entry.row.id}
            entry={entry}
            state={rowStateOf(view, entry)}
            version={versionName(entry.row.current_version_id)}
            pendingDraft={
              entry.row.pending_draft_id != null ? draftOf(entry.row.pending_draft_id) : undefined
            }
          />
        ))}
      </ul>
      {removals.length > 0 ? (
        <div>
          <h3 className="mb-2 font-medium text-sm">Deleted, still on the machine</h3>
          <ul className="space-y-2" data-testid="board-removals">
            {removals.map(({ row, state }) => (
              <li
                key={row.id}
                className="rounded-lg border border-border border-dashed p-3"
                data-testid="board-removal"
              >
                <p className="break-words font-medium text-sm">{row.label}</p>
                <StateLine state={state} />
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

function BoardRowCard({
  entry,
  state,
  version,
  pendingDraft,
}: {
  entry: BoardRowView;
  state: RowState;
  version: string | null;
  pendingDraft: ProfileDraft | undefined;
}) {
  const { row } = entry;
  const home = useSetHomeScreen();
  const remove = useDeleteBoardRow();
  const [confirming, setConfirming] = useState(false);
  // Closing the confirm hands focus back to Delete, which stays rendered under it.
  const deleteRef = useRef<HTMLButtonElement>(null);
  const [restoreFocus, setRestoreFocus] = useState(false);
  useEffect(() => {
    if (restoreFocus && !confirming) {
      deleteRef.current?.focus();
      setRestoreFocus(false);
    }
  }, [restoreFocus, confirming]);
  const closeConfirm = () => {
    setConfirming(false);
    setRestoreFocus(true);
  };
  const owner = ownerOf(row);
  const busy = home.isPending || remove.isPending;

  return (
    <li className="rounded-lg border border-border p-3" data-testid="board-row" data-owner={owner}>
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
        <p className="min-w-0 break-words font-medium text-sm">{row.label}</p>
        <Badge variant={owner === "app" ? "secondary" : "outline"}>
          {owner === "app" ? "the app's" : "yours"}
        </Badge>
        {entry.machine.selected ? <Badge>selected</Badge> : null}
        <span className="text-muted-foreground text-xs">
          {entry.type}
          {entry.utility ? " · utility" : ""} ·{" "}
          {version ? (
            <>
              version <span className="font-mono">{version}</span>
            </>
          ) : (
            "version not loaded"
          )}
        </span>
      </div>

      <StateLine state={state} />
      {row.pending_set_id != null ? (
        <p className="mt-1 text-muted-foreground text-xs" data-testid="board-row-set">
          {pendingDraft?.set_name
            ? `Once it is on the machine it is recorded as ${
                (row.pending_major
                  ? pendingDraft.set_next_major_label
                  : pendingDraft.set_next_minor_label) ?? "the next version"
              } of ${pendingDraft.set_name}.`
            : "Once it is on the machine it is recorded as the next version of its Set."}
        </p>
      ) : null}

      <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2">
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            className="size-4"
            checked={row.on_home_screen}
            disabled={busy}
            aria-label={`${row.label} on the machine's home screen`}
            data-testid="board-home-screen"
            onChange={(event) => home.mutate({ rowId: row.id, on: event.target.checked })}
          />
          On the home screen
        </label>
        <Button
          ref={deleteRef}
          size="sm"
          variant="ghost"
          disabled={busy}
          aria-label={`Delete ${row.label}`}
          data-testid="board-delete"
          onClick={() => setConfirming(true)}
        >
          <Trash2 className="size-3.5" aria-hidden="true" />
          Delete
        </Button>
      </div>

      {confirming ? (
        <div className="mt-3">
          <ConfirmStrip
            title={`Delete ${row.label} from the board?`}
            confirmLabel="Delete from the board"
            testId="board-delete-confirm"
            focusOnOpen
            onCancel={closeConfirm}
            onConfirm={() => {
              closeConfirm();
              remove.mutate(row.id);
            }}
          >
            {owner === "app"
              ? "The next sync removes the app's copy from the machine, as long as it is still as the app saved it. The copy stays while a Set is still brewing it, while it is the profile selected on the machine and nothing else on the board can replace it, and while another profile on the board stands on it. "
              : "This profile is yours, so it stays on the machine and only leaves the board. "}
            A profile of yours is never removed from the machine.
          </ConfirmStrip>
        </div>
      ) : null}
    </li>
  );
}

function StateLine({ state }: { state: RowState }) {
  const Icon = state.tone === "ok" ? Check : state.tone === "info" ? Clock : AlertTriangle;
  return (
    <div className="mt-1" data-testid="board-state" data-tone={state.tone}>
      <p
        className={cn(
          "flex items-start gap-1.5 text-sm",
          state.tone === "warn" ? "text-status-warn-text" : "",
        )}
      >
        <Icon className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
        <span className="min-w-0 break-words">{state.text}</span>
      </p>
      {state.notes.length > 0 ? (
        <ul className="mt-0.5 space-y-0.5 pl-5 text-muted-foreground text-xs">
          {state.notes.map((note) => (
            <li key={note} className="break-words">
              {note}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
