import { useQueries } from "@tanstack/react-query";
import { AlertTriangle, SlidersHorizontal, Upload } from "lucide-react";
import { type ChangeEvent, useEffect, useMemo, useRef, useState } from "react";
import { useLocation } from "react-router-dom";
import { getBoardVersions } from "@/api/client";
import type { BoardProposal, BoardRowView } from "@/api/types";
import { EmptyState } from "@/components/layout/EmptyState";
import { PageHeader } from "@/components/layout/PageHeader";
import { NewProfileRow } from "@/components/profiles/NewProfileRow";
import { ProfileRow } from "@/components/profiles/ProfileRow";
import { ResetBanner } from "@/components/profiles/ResetBanner";
import { ImportResults } from "@/components/shots/ImportResults";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useProfileBoard } from "@/hooks/useBoard";
import { useImportFiles } from "@/hooks/useImport";
import { useQueryErrorToast } from "@/hooks/useQueryErrorToast";
import { queryKeys } from "@/lib/queryKeys";

/**
 * Profiles: one list of every profile, whether it should be on the machine, and a dropdown of
 * its versions.
 *
 * A row says what the profile is, whether the machine holds it and what the next sync will do
 * about it, with two switches: **On the machine** (the sync puts it there or removes it) and
 * **Starred** (the machine's home-screen carousel). A row opens to its versions, newest first,
 * each with a **Make active** button; proposals from the agent sit at the top of the profile
 * they would join (a proposed new profile is a row of its own), and a profile whose file was
 * edited outside the app opens on a conflict with both sides to choose from.
 *
 * Nothing here writes to the machine: every control edits the list, and the next sync, with
 * the Writes switch on, makes the machine hold exactly the profiles that are on. Rows that are
 * off are hidden unless asked for, except the ones that need a person (a conflict, a proposal)
 * and the ones seen on this visit, so a profile switched off does not vanish under the cursor.
 */

/** Profile exports only: a shot dropped here is imported too, and says so. */
const UPLOAD_ACCEPT = ".json,application/json";
const SHOW_OFF_KEY = "gaggiclanker.profiles.showOff";

function readShowOff(): boolean {
  try {
    return window.localStorage.getItem(SHOW_OFF_KEY) === "1";
  } catch {
    return false;
  }
}

function rememberShowOff(value: boolean): void {
  try {
    window.localStorage.setItem(SHOW_OFF_KEY, value ? "1" : "0");
  } catch {
    // A private window or blocked storage: the choice just lasts until the page is left.
  }
}

/** The row a proposal lands on, or `null` for a proposed new profile. */
function proposalsByRow(proposals: BoardProposal[]): Map<number, BoardProposal[]> {
  const byRow = new Map<number, BoardProposal[]>();
  for (const proposal of proposals) {
    if (proposal.row_id == null) continue;
    byRow.set(proposal.row_id, [...(byRow.get(proposal.row_id) ?? []), proposal]);
  }
  return byRow;
}

function needsAPerson(entry: BoardRowView): boolean {
  return entry.in_conflict || (entry.proposed_versions ?? 0) > 0;
}

export function ProfilesPage() {
  const board = useProfileBoard();
  const importFiles = useImportFiles();
  const uploadRef = useRef<HTMLInputElement>(null);
  const { hash, key: arrivalKey } = useLocation();
  const [showOff, setShowOff] = useState(readShowOff);
  const [open, setOpen] = useState<string | null>(null);
  // Rows shown at any point in this visit stay shown, so switching one off does not make it vanish.
  const [seen, setSeen] = useState<ReadonlySet<number>>(new Set());

  useQueryErrorToast(board.error, "Could not load the profiles");

  const view = board.data;
  const rows = view?.rows ?? [];
  const proposals = view?.proposals ?? [];
  const byRow = useMemo(() => proposalsByRow(proposals), [proposals]);
  const newProposals = proposals.filter((p) => p.row_id == null);

  const visible = (entry: BoardRowView) =>
    showOff || entry.on_machine || needsAPerson(entry) || seen.has(entry.row.id);
  const shown = rows.filter(visible).sort(
    (a, b) =>
      Number(b.in_conflict) - Number(a.in_conflict) ||
      // Utility profiles (a backflush) are not what you brew with: last.
      Number(a.utility) - Number(b.utility) ||
      a.row.label.localeCompare(b.row.label, undefined, { sensitivity: "base" }),
  );
  const hiddenCount = rows.length - shown.length;

  // Remember what has been shown (a state update only when something new appears).
  const shownKey = shown.map((e) => e.row.id).join(",");
  // biome-ignore lint/correctness/useExhaustiveDependencies: `shownKey` stands for `shown`
  useEffect(() => {
    setSeen((previous) => {
      const next = new Set(previous);
      for (const entry of shown) next.add(entry.row.id);
      return next.size === previous.size ? previous : next;
    });
  }, [shownKey]);

  // `#version-N` (a shot's profile version, an import's result) opens the profile that has it;
  // that needs every profile's versions, so they are read only for such a link.
  const versionWanted = /^#version-(\d+)$/.exec(hash)?.[1];
  const versionReads = useQueries({
    queries: rows.map((entry) => ({
      queryKey: queryKeys.board.versions(entry.row.id),
      queryFn: () => getBoardVersions(entry.row.id),
      enabled: versionWanted !== undefined,
    })),
  });
  const versionRow =
    versionWanted === undefined
      ? undefined
      : rows.find((_, index) =>
          versionReads[index]?.data?.versions.some((v) => String(v.version_id) === versionWanted),
        )?.row.id;

  // Links that used to land on `#staged` (a draft somebody just made) open the newest
  // proposal's row; `#version-N` opens the profile that has the version.
  const target = useMemo(() => {
    if (hash === "#staged") {
      const newest = [...proposals].sort((a, b) => b.draft.id - a.draft.id)[0];
      if (!newest) return null;
      return newest.row_id != null ? `row-${newest.row_id}` : `new-${newest.draft.id}`;
    }
    if (versionRow !== undefined) return `row-${versionRow}`;
    return null;
  }, [hash, proposals, versionRow]);

  // Acts once per arrival (per navigation), the first time the link has something to open. The
  // target itself moves as proposals come and go (declining one makes another the newest), and
  // a row the person closed or a proposal they just answered must not be reopened by it.
  const handled = useRef<string | null>(null);
  useEffect(() => {
    if (target === null || handled.current === arrivalKey) return;
    handled.current = arrivalKey;
    setOpen(target);
    if (target.startsWith("row-")) {
      const id = Number(target.slice(4));
      setSeen((previous) => new Set(previous).add(id));
    }
    // After the row has rendered (it may only now be visible).
    const frame = requestAnimationFrame(() => {
      const id = target.startsWith("row-")
        ? `profile-${target.slice(4)}`
        : `proposal-${target.slice(4)}`;
      document.getElementById(id)?.scrollIntoView?.({ block: "start", behavior: "smooth" });
    });
    return () => cancelAnimationFrame(frame);
  }, [target, arrivalKey]);

  function onUpload(event: ChangeEvent<HTMLInputElement>) {
    const files = Array.from(event.target.files ?? []);
    // Cleared, so choosing the same file twice fires a change event both times.
    event.target.value = "";
    if (files.length > 0) importFiles.mutate({ files });
  }

  return (
    <div className="space-y-4">
      <PageHeader
        title="Profiles"
        subtitle="Every profile, and whether it should be on the machine. Open one for its versions."
        actions={
          <Button
            variant="outline"
            size="sm"
            onClick={() => uploadRef.current?.click()}
            disabled={importFiles.isPending}
          >
            <Upload className="size-3.5" aria-hidden="true" />
            {importFiles.isPending ? "Uploading…" : "Upload profile"}
          </Button>
        }
      />
      <input
        ref={uploadRef}
        type="file"
        multiple
        accept={UPLOAD_ACCEPT}
        className="hidden"
        onChange={onUpload}
        data-testid="profile-upload-input"
        aria-label="Profile export files to upload"
      />
      {importFiles.data ? <ImportResults summary={importFiles.data} /> : null}

      {view ? (
        <>
          <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
            <p
              className="text-muted-foreground text-sm"
              data-testid="writes-line"
              data-writes={view.writes_enabled ? "on" : "off"}
            >
              {view.writes_enabled
                ? "Writes are on: the next sync updates the machine."
                : "Writes are off: the machine will follow when they are on."}
            </p>
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                className="size-4"
                checked={showOff}
                data-testid="show-off"
                onChange={(event) => {
                  setShowOff(event.target.checked);
                  rememberShowOff(event.target.checked);
                }}
              />
              Show profiles that are off
            </label>
          </div>

          {!view.adopted ? (
            <p className="text-muted-foreground text-sm" data-testid="not-adopted">
              The first sync with writes on takes the machine's profiles into this list and writes
              nothing.
            </p>
          ) : null}

          <ResetBanner view={view} />

          {newProposals.length === 0 && shown.length === 0 ? (
            <EmptyState
              icon={SlidersHorizontal}
              title={rows.length === 0 ? "No profiles yet" : "Every profile is off"}
              description={
                rows.length === 0
                  ? "Profiles appear here after a sync reads the machine, or when you upload one."
                  : "Switch on “Show profiles that are off” to see them."
              }
            />
          ) : (
            <ul className="space-y-2" data-testid="profile-list">
              {newProposals.map((proposal) => (
                <NewProfileRow
                  key={`new-${proposal.draft.id}`}
                  proposal={proposal}
                  open={open === `new-${proposal.draft.id}`}
                  onToggle={() =>
                    setOpen((current) =>
                      current === `new-${proposal.draft.id}` ? null : `new-${proposal.draft.id}`,
                    )
                  }
                />
              ))}
              {shown.map((entry) => (
                <ProfileRow
                  key={entry.row.id}
                  view={view}
                  entry={entry}
                  open={open === `row-${entry.row.id}`}
                  proposals={byRow.get(entry.row.id) ?? []}
                  onToggle={() =>
                    setOpen((current) =>
                      current === `row-${entry.row.id}` ? null : `row-${entry.row.id}`,
                    )
                  }
                />
              ))}
            </ul>
          )}

          {hiddenCount > 0 && !showOff ? (
            <p className="text-muted-foreground text-sm" data-testid="hidden-count">
              {hiddenCount} {hiddenCount === 1 ? "profile is" : "profiles are"} off and hidden.
            </p>
          ) : null}
        </>
      ) : board.isError ? (
        <div
          className="rounded-md border border-status-warn/40 bg-status-warn/10 p-3"
          data-testid="board-unreadable"
          role="alert"
        >
          <p className="flex items-center gap-1.5 font-medium text-sm text-status-warn-text">
            <AlertTriangle className="size-3.5" aria-hidden="true" />
            Can't read the profiles right now
          </p>
          <Button
            className="mt-2"
            size="sm"
            variant="outline"
            disabled={board.isFetching}
            onClick={() => void board.refetch()}
          >
            Try again
          </Button>
        </div>
      ) : (
        <div className="space-y-2" data-testid="profiles-skeleton">
          {[0, 1, 2].map((row) => (
            <Skeleton key={row} className="h-16 w-full" />
          ))}
        </div>
      )}
    </div>
  );
}
