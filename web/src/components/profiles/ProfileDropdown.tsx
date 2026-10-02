import { FilePen } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import type { BoardProposal, BoardRowView, ListedVersion, ProfileVersionsView } from "@/api/types";
import { ProfileDiff } from "@/components/drafts/ProfileDiff";
import { ProfileJsonEditor } from "@/components/drafts/ProfileJsonEditor";
import { ProfileSummary } from "@/components/drafts/ProfileSummary";
import { ConflictPanel } from "@/components/profiles/ConflictPanel";
import { ProposalPanel } from "@/components/profiles/ProposalPanel";
import { ConfirmStrip } from "@/components/sync/ConfirmStrip";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useBoardVersions, useSetActiveVersion } from "@/hooks/useBoard";
import { useSingleFlight } from "@/hooks/useSingleFlight";
import { sourceWords } from "@/lib/board";
import { formatTime } from "@/lib/shots";

type Json = Record<string, unknown>;

/**
 * What a row opens: a conflict first (it blocks the profile), then the proposals waiting for a
 * person, then the profile's versions, newest first. Each version says when it was added, where
 * it came from, what brewed with it, whether it is the active one (or offers **Make active**),
 * **Edit a copy**, and its information: the first version is shown as a summary because there is
 * nothing before it, every later one as what changed from the version before it in this list.
 *
 * Nothing here writes to the machine. A version made active, and an edited copy once it is made
 * active, reach the machine on the next sync.
 */
export function ProfileDropdown({
  entry,
  proposals,
}: {
  entry: BoardRowView;
  /** The board's proposals that would land on this profile, for where each would land. */
  proposals: BoardProposal[];
}) {
  const rowId = entry.row.id;
  const versions = useBoardVersions(rowId, true);
  const [editing, setEditing] = useState<ListedVersion | null>(null);

  if (versions.isPending) {
    return (
      <div className="space-y-2 p-3" data-testid="dropdown-loading">
        <Skeleton className="h-16 w-full" />
        <Skeleton className="h-16 w-full" />
      </div>
    );
  }
  if (versions.isError || !versions.data) {
    return (
      <p className="p-3 text-muted-foreground text-sm" role="alert" data-testid="dropdown-error">
        The versions could not be read right now.
      </p>
    );
  }
  const view = versions.data;
  const landingOf = (draftId: number) => proposals.find((p) => p.draft.id === draftId)?.landing;
  const documentOf = (versionId: number): Json | null =>
    (view.versions.find((v) => v.version_id === versionId)?.profile as Json | undefined) ?? null;

  return (
    <div className="min-w-0 space-y-3 border-border border-t p-3" data-testid="profile-dropdown">
      {entry.in_conflict ? <ConflictPanel rowId={rowId} /> : null}

      {(view.proposed ?? []).map((proposed) => (
        <ProposalPanel
          key={proposed.draft.id}
          draft={proposed.draft}
          profile={proposed.profile as Json}
          base={documentOf(proposed.compared_to_version_id)}
          landing={landingOf(proposed.draft.id)}
          rowOn={entry.on_machine}
        />
      ))}

      <ul className="space-y-2" data-testid="version-list">
        {view.versions.map((version) => (
          <VersionItem
            key={version.version_id}
            entry={entry}
            version={version}
            view={view}
            onEdit={() => setEditing(version)}
          />
        ))}
      </ul>

      {editing ? (
        <ProfileJsonEditor
          open
          onOpenChange={(open) => {
            if (!open) setEditing(null);
          }}
          baseVersionId={editing.version_id}
          label={editing.label}
          document={editing.profile as Record<string, unknown>}
        />
      ) : null}
    </div>
  );
}

function VersionItem({
  entry,
  version,
  view,
  onEdit,
}: {
  entry: BoardRowView;
  version: ListedVersion;
  view: ProfileVersionsView;
  onEdit: () => void;
}) {
  const makeActive = useSetActiveVersion();
  const [confirming, setConfirming] = useState(false);
  const [showAll, setShowAll] = useState(false);
  const sets = entry.sets_brewing ?? [];
  const first = version.previous_version_id == null;
  // Only ever the version before this one in this list, never another.
  const previous = first
    ? null
    : (view.versions.find((v) => v.version_id === version.previous_version_id) ?? null);
  const mine = makeActive.variables?.rowId === entry.row.id;
  const busy = makeActive.isPending && mine;

  const once = useSingleFlight();
  const choose = () =>
    once((release) =>
      makeActive.mutate(
        { rowId: entry.row.id, versionId: version.version_id },
        { onSettled: release },
      ),
    );

  return (
    <li
      className="min-w-0 space-y-2 rounded-md border border-border p-3"
      data-testid="version"
      data-active={version.is_active ? "yes" : "no"}
      data-version-id={version.version_id}
      id={`version-${version.version_id}`}
    >
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        {version.is_active ? (
          <Badge data-testid="active-marker">Active</Badge>
        ) : (
          <Button
            size="sm"
            variant="outline"
            disabled={busy}
            data-testid="make-active"
            onClick={() => (sets.length > 0 ? setConfirming(true) : choose())}
          >
            Make active
          </Button>
        )}
        <span className="text-muted-foreground text-xs">{formatTime(version.added_at)}</span>
        <span className="text-muted-foreground text-xs">· {sourceWords(version.source)}</span>
        {version.is_on_machine ? <Badge variant="outline">On the machine now</Badge> : null}
      </div>

      <p className="text-muted-foreground text-xs">
        {version.shots_brewed > 0 ? (
          <Link
            className="underline underline-offset-2"
            to={`/shots?profile_version_id=${version.version_id}`}
          >
            {version.shots_brewed} {version.shots_brewed === 1 ? "shot" : "shots"}
          </Link>
        ) : (
          "No shots yet"
        )}
        {(version.sets_brewing ?? []).length > 0 ? (
          <>
            {" "}
            · brewed by{" "}
            {(version.sets_brewing ?? []).map((set, index) => (
              <span key={set.set_id}>
                {index > 0 ? ", " : ""}
                <Link className="underline underline-offset-2" to={`/sets/${set.set_id}`}>
                  {set.name}
                </Link>
              </span>
            ))}
          </>
        ) : null}
        {version.did_not_verify
          ? " · did not read back as sent; not tried again until made active"
          : ""}
      </p>

      {confirming ? (
        <ConfirmStrip
          title="Make this version active?"
          confirmLabel="Make active"
          confirmVariant="default"
          testId="make-active-confirm"
          focusOnOpen
          onCancel={() => setConfirming(false)}
          onConfirm={() => {
            setConfirming(false);
            choose();
          }}
        >
          {sets.map((s) => s.name).join(", ")} brew{sets.length === 1 ? "s" : ""} this profile. The
          next sync puts this version on the machine, so that is what{" "}
          {sets.length === 1 ? "it brews" : "they brew"} from then on.
        </ConfirmStrip>
      ) : null}

      <div data-testid="version-information">
        {first || previous === null ? (
          <ProfileSummary profile={version.profile as Json} />
        ) : (
          <div className="space-y-1">
            <p className="text-muted-foreground text-xs">Changed from the version before it</p>
            <ProfileDiff
              base={previous.profile as Json}
              draft={version.profile as Json}
              empty="Nothing the list shows differs from the version before it."
            />
            <Button
              size="sm"
              variant="ghost"
              data-testid="show-whole-profile"
              onClick={() => setShowAll((open) => !open)}
            >
              {showAll ? "Hide the whole profile" : "Show the whole profile"}
            </Button>
            {showAll ? <ProfileSummary profile={version.profile as Json} /> : null}
          </div>
        )}
      </div>

      <div className="flex flex-wrap gap-2">
        <Button
          size="sm"
          variant="ghost"
          data-testid="edit-a-copy"
          aria-label={`Edit a copy of the version from ${formatTime(version.added_at)}`}
          onClick={onEdit}
        >
          <FilePen className="size-3.5" aria-hidden="true" />
          Edit a copy
        </Button>
      </div>
    </li>
  );
}
