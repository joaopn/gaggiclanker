import { AlertTriangle, FilePen, SlidersHorizontal, Star, Upload } from "lucide-react";
import { type ChangeEvent, useEffect, useRef, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import type { ProfileDraft, ProfileVersionSummary } from "@/api/types";
import { DraftCard } from "@/components/drafts/DraftCard";
import { ProfileJsonEditor } from "@/components/drafts/ProfileJsonEditor";
import { EmptyState } from "@/components/layout/EmptyState";
import { PageHeader } from "@/components/layout/PageHeader";
import { SectionCard } from "@/components/layout/SectionCard";
import { ImportResults } from "@/components/shots/ImportResults";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useProfiles, useProfileVersion, useProfileVersions } from "@/hooks/useArchive";
import { useDeviceWrites } from "@/hooks/useDeviceStatus";
import { useProfileDrafts, useStageVersionAsIs } from "@/hooks/useDrafts";
import { useImportFiles } from "@/hooks/useImport";
import { useQueryErrorToast } from "@/hooks/useQueryErrorToast";
import { formatDate } from "@/lib/shots";

/**
 * Profiles: what is on the machine, what is on its way there, and everything
 * the archive has ever held.
 *
 * The three sections are one story read downwards. "On the machine" is the
 * mirror. "Staged for the machine" is the queue of drafts — profiles proposed
 * for the machine and what stands in the way of each — and it lives here
 * rather than on a page of its own because staging is a step between a version
 * and the machine, not a destination: everything that creates a draft starts
 * from something on this page or ends by linking back to it. "Versions" is
 * every document a shot can resolve to, and the two ways to stage one.
 *
 * Nothing here writes to the machine. "Edit" and "Stage as is" both produce a
 * **draft**, which somebody approves and pushes from its card, because a
 * profile with zero phases crashes brew start on the display and a float
 * `pump: 100.0` is parsed as an object with zero targets — neither is
 * something a text box should be able to reach the machine with in one click.
 */

/** Profile exports only: a shot dropped here is imported too, and says so. */
const UPLOAD_ACCEPT = ".json,application/json";

export function ProfilesPage() {
  const profiles = useProfiles();
  const versions = useProfileVersions({ limit: 200 });
  const [editing, setEditing] = useState<number | null>(null);
  const editingVersion = useProfileVersion(editing ?? undefined);
  const [showAllDrafts, setShowAllDrafts] = useState(false);
  const drafts = useProfileDrafts(showAllDrafts ? {} : { open: true });
  // Asked for separately from the list above, which the toggle can widen to
  // everything ever staged. The banner is about work that is stuck, and a page
  // showing six pushed drafts and nothing open is not stuck. When the toggle is
  // off these are the same query key, so it costs no second request — and
  // "open" stays the server's definition of open rather than a second one here.
  const openDrafts = useProfileDrafts({ open: true });
  const writes = useDeviceWrites();
  const importFiles = useImportFiles();
  const uploadRef = useRef<HTMLInputElement>(null);
  const { hash } = useLocation();

  useQueryErrorToast(profiles.error, "Could not load profiles");
  useQueryErrorToast(versions.error, "Could not load profile versions");
  useQueryErrorToast(drafts.error, "Could not load the staging queue");

  const draftItems = drafts.data?.items ?? [];
  // The banner is about a queue that will not move, so it only speaks when
  // there is a queue. A page with nothing open has nothing to warn about, and a
  // permanent warning is one nobody reads by the second week.
  const writesBlocked = writes.data?.enabled === false && (openDrafts.data?.items.length ?? 0) > 0;

  // Everything that creates a draft elsewhere — an accepted analysis
  // suggestion, the starting-point wizard, the JSON editor, a chat tool — comes
  // back here with `#staged`. Landing at the top of a long page and leaving the
  // reader to find the thing they just made is half a link.
  const stagedArrived = !drafts.isPending;
  useEffect(() => {
    if (hash !== "#staged" || !stagedArrived) return;
    document.getElementById("staged")?.scrollIntoView({ block: "start", behavior: "smooth" });
  }, [hash, stagedArrived]);

  function onUpload(event: ChangeEvent<HTMLInputElement>) {
    const files = Array.from(event.target.files ?? []);
    // Cleared, so choosing the same file twice fires a change event both
    // times — which here is a deliberate retry, not a mistake.
    event.target.value = "";
    if (files.length > 0) importFiles.mutate({ files });
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Profiles"
        subtitle="Mirrored from the machine. Each distinct version is kept, so a shot from March still resolves to what it was brewed with."
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
      {/* The same importer the shots page uses: a profile export becomes a
          version, and the version appears below with its staging button. */}
      {importFiles.data ? <ImportResults summary={importFiles.data} /> : null}

      {writesBlocked ? (
        <div
          className="rounded-md border border-status-warn/40 bg-status-warn/10 p-3"
          data-testid="writes-disabled-banner"
        >
          <p className="flex items-center gap-1.5 font-medium text-sm text-status-warn-text">
            <AlertTriangle className="size-3.5" aria-hidden="true" />
            Writing to the machine is switched off
          </p>
          <p className="mt-1 text-status-warn-text text-xs">
            Staging and approving work; pushing is refused before anything reaches the wire, and the
            refusal is recorded in the write audit. Turn on "Device writes enabled" under{" "}
            <Link className="underline underline-offset-2" to="/settings">
              Settings → Machine
            </Link>
            .
          </p>
        </div>
      ) : null}

      {profiles.isPending ? (
        <div className="space-y-2">
          {[0, 1, 2].map((row) => (
            <Skeleton key={row} className="h-8 w-full" />
          ))}
        </div>
      ) : profiles.data && profiles.data.items.length > 0 ? (
        <SectionCard
          title="On the machine"
          description="The mirror, as of the last pull."
          contentClassName="overflow-x-auto"
        >
          <table className="w-full border-collapse text-left text-sm">
            <thead className="border-border border-b text-muted-foreground text-xs uppercase tracking-wide">
              <tr>
                <th className="py-2 pr-4 font-medium">Profile</th>
                <th className="py-2 pr-4 font-medium">Type</th>
                <th className="py-2 pr-4 text-right font-medium">Shots</th>
                <th className="py-2 pr-4 font-medium">Version</th>
                <th className="py-2 font-medium">State</th>
              </tr>
            </thead>
            <tbody>
              {profiles.data.items.map((profile) => (
                <tr
                  key={profile.device_id}
                  className="border-border border-b last:border-0 hover:bg-muted/40"
                >
                  <td className="py-2 pr-4">
                    <span className="block max-w-[18rem] truncate font-medium">
                      {profile.label}
                    </span>
                    <span className="text-muted-foreground text-xs">{profile.device_id}</span>
                  </td>
                  <td className="py-2 pr-4">{profile.type}</td>
                  <td className="py-2 pr-4 text-right tabular-nums">{profile.shot_count}</td>
                  <td className="py-2 pr-4 font-mono text-xs">
                    {/* The content hash, short. It is what makes "the same
                        profile twice" one row and an edited phase a new one. */}
                    {profile.content_hash.slice(0, 8)}
                  </td>
                  <td className="py-2">
                    <div className="flex flex-wrap gap-1">
                      {profile.selected ? <Badge>selected</Badge> : null}
                      {profile.favorite ? (
                        <Badge variant="secondary" className="gap-1">
                          <Star className="size-3" aria-hidden="true" />
                          favourite
                        </Badge>
                      ) : null}
                      {profile.utility ? <Badge variant="outline">utility</Badge> : null}
                      {profile.deleted_at ? <Badge variant="outline">removed</Badge> : null}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </SectionCard>
      ) : (
        <EmptyState
          icon={SlidersHorizontal}
          title="No profiles mirrored yet"
          description="The mirror is read on a pull, and whenever the machine reports that the selected profile changed."
        />
      )}

      <StagedForTheMachine
        items={draftItems}
        pending={drafts.isPending}
        showAll={showAllDrafts}
        onShowAllChange={setShowAllDrafts}
      />

      <ProfileVersions
        items={versions.data?.items ?? []}
        total={versions.data?.total ?? 0}
        pending={versions.isPending}
        onEdit={setEditing}
      />

      {editing !== null && editingVersion.data?.profile ? (
        <ProfileJsonEditor
          open
          onOpenChange={(open) => {
            if (!open) setEditing(null);
          }}
          baseVersionId={editing}
          label={editingVersion.data.label}
          document={editingVersion.data.profile as Record<string, unknown>}
        />
      ) : null}
    </div>
  );
}

/**
 * The staging queue: what has been proposed for the machine, and what is left
 * to decide about each one.
 *
 * `open` is the default view and it includes `failed` on purpose — a push that
 * did not verify left a profile on the display that somebody has to decide
 * about, and filing it under "done" is how it stays there for a month.
 */
function StagedForTheMachine({
  items,
  pending,
  showAll,
  onShowAllChange,
}: {
  items: ProfileDraft[];
  pending: boolean;
  showAll: boolean;
  onShowAllChange: (next: boolean) => void;
}) {
  return (
    // The id is the anchor every "I made you a draft" link in the app points
    // at; keep it even when the section is empty, or the link lands nowhere.
    <section id="staged" className="scroll-mt-20">
      <SectionCard
        title="Staged for the machine"
        description="Every one is saved as a new profile with an [AI] suffix — nothing is ever overwritten, and nothing is ever selected for you."
        actions={
          <Button variant="outline" size="sm" onClick={() => onShowAllChange(!showAll)}>
            {showAll ? "Show only what is open" : "Show everything"}
          </Button>
        }
      >
        {pending ? (
          <div className="space-y-3" data-testid="drafts-skeleton">
            <Skeleton className="h-32 w-full" />
            <Skeleton className="h-32 w-full" />
          </div>
        ) : items.length === 0 ? (
          <p className="text-muted-foreground text-sm" data-testid="staged-empty">
            {showAll
              ? "Nothing has ever been staged."
              : "Nothing staged. Stage a version below, or accept a profile suggestion from an analysis."}
          </p>
        ) : (
          <ul className="space-y-3" data-testid="draft-list">
            {items.map((draft) => (
              <DraftCard key={draft.id} draft={draft} />
            ))}
          </ul>
        )}
      </SectionCard>
    </section>
  );
}

/**
 * Every stored version, mirrored or imported.
 *
 * The table above answers "what is on the machine". This answers "what can a
 * shot resolve to", which is a superset and the one that matters for the
 * archive: a profile edited on the display leaves its previous version behind,
 * and a version loaded from a file never had a device profile at all. Before
 * `/api/profile-versions` existed, an imported profile landed in the database
 * and appeared nowhere.
 */
function ProfileVersions({
  items,
  total,
  pending,
  onEdit,
}: {
  items: ProfileVersionSummary[];
  total: number;
  pending: boolean;
  /** Open the draft editor on this version. */
  onEdit: (versionId: number) => void;
}) {
  const stage = useStageVersionAsIs();

  if (pending) return <Skeleton className="h-24 w-full" />;
  if (items.length === 0) return null;
  return (
    <SectionCard
      title="Versions"
      description={`Every distinct profile document the archive holds (${total}). A version is immutable and content-hashed, so a shot from March still resolves to what it was brewed with.`}
      contentClassName="overflow-x-auto"
    >
      <table className="w-full border-collapse text-left text-sm">
        <thead className="border-border border-b text-muted-foreground text-xs uppercase tracking-wide">
          <tr>
            <th className="py-2 pr-4 font-medium">Label</th>
            <th className="py-2 pr-4 font-medium">Source</th>
            <th className="py-2 pr-4 font-medium">Created</th>
            <th className="py-2 pr-4 text-right font-medium">Shots</th>
            <th className="py-2 pr-4 font-medium">Hash</th>
            <th className="py-2 pr-4 font-medium">On the machine</th>
            <th className="py-2 font-medium">
              <span className="sr-only">Stage or edit</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {items.map((version) => (
            <tr
              key={version.id}
              id={`version-${version.id}`}
              data-testid="profile-version-row"
              className="border-border border-b last:border-0 target:bg-muted/60"
            >
              <td className="py-2 pr-4">
                <span className="block max-w-[18rem] truncate font-medium">{version.label}</span>
                <span className="text-muted-foreground text-xs">
                  {version.type}
                  {version.utility ? " · utility" : ""}
                </span>
              </td>
              <td className="py-2 pr-4">
                {version.source === "import" ? (
                  <Badge variant="secondary">imported</Badge>
                ) : (
                  <span className="text-muted-foreground">device</span>
                )}
              </td>
              <td className="py-2 pr-4">{formatDate(version.created_at)}</td>
              <td className="py-2 pr-4 text-right tabular-nums">
                {version.shot_count > 0 ? (
                  <Link
                    className="underline underline-offset-2"
                    to={`/shots?profile_version_id=${version.id}`}
                  >
                    {version.shot_count}
                  </Link>
                ) : (
                  version.shot_count
                )}
              </td>
              <td className="py-2 pr-4 font-mono text-xs">{version.content_hash.slice(0, 8)}</td>
              <td className="py-2 pr-4">
                {version.mirrored ? (
                  <Badge variant="outline">mirrored</Badge>
                ) : (
                  <span className="text-muted-foreground text-xs">not on the machine</span>
                )}
              </td>
              <td className="py-2">
                <div className="flex justify-end gap-1">
                  <Button
                    size="sm"
                    variant="ghost"
                    data-testid="stage-as-is"
                    disabled={stage.isPending}
                    aria-label={`Stage ${version.label} as is`}
                    onClick={() => stage.mutate({ versionId: version.id, label: version.label })}
                  >
                    Stage as is
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    data-testid="edit-as-draft"
                    onClick={() => onEdit(version.id)}
                    aria-label={`Edit ${version.label} as a draft`}
                  >
                    <FilePen className="size-3.5" aria-hidden="true" />
                    Edit
                  </Button>
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </SectionCard>
  );
}
