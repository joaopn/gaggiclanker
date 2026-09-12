import { FilePen, SlidersHorizontal, Star } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import type { ProfileVersionSummary } from "@/api/types";
import { ProfileJsonEditor } from "@/components/drafts/ProfileJsonEditor";
import { EmptyState } from "@/components/layout/EmptyState";
import { PageHeader } from "@/components/layout/PageHeader";
import { SectionCard } from "@/components/layout/SectionCard";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useProfiles, useProfileVersion, useProfileVersions } from "@/hooks/useArchive";
import { useQueryErrorToast } from "@/hooks/useQueryErrorToast";
import { formatDate } from "@/lib/shots";

/**
 * The profile mirror, plus the one way a profile gets *written*.
 *
 * Reading is the bulk of this page and always was. Editing, added later,
 * never touches the machine from here: "Edit as draft" opens a JSON editor,
 * validates against the strict schema and the safety policy on the server, and
 * produces a **draft**. Drafts are approved and pushed from their own page,
 * because a profile with zero phases crashes brew start on the display and a
 * float `pump: 100.0` is parsed as an object with zero targets — neither is
 * something a text box
 * should be able to reach the machine with in one click.
 */
export function ProfilesPage() {
  const profiles = useProfiles();
  const versions = useProfileVersions({ limit: 200 });
  const [editing, setEditing] = useState<number | null>(null);
  const editingVersion = useProfileVersion(editing ?? undefined);

  useQueryErrorToast(profiles.error, "Could not load profiles");
  useQueryErrorToast(versions.error, "Could not load profile versions");

  return (
    <div className="space-y-6">
      <PageHeader
        title="Profiles"
        subtitle="Mirrored from the machine. Each distinct version is kept, so a shot from March still resolves to what it was brewed with."
      />

      {profiles.isPending ? (
        <div className="space-y-2">
          {[0, 1, 2].map((row) => (
            <Skeleton key={row} className="h-8 w-full" />
          ))}
        </div>
      ) : profiles.data && profiles.data.items.length > 0 ? (
        <div className="overflow-x-auto">
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
                  key={`${profile.machine_id}:${profile.device_id}`}
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
        </div>
      ) : (
        <EmptyState
          icon={SlidersHorizontal}
          title="No profiles mirrored yet"
          description="The mirror is read on connect, whenever the selected profile changes, and every fifteen minutes."
        />
      )}

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
              <span className="sr-only">Edit as draft</span>
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
                <Button
                  size="sm"
                  variant="ghost"
                  data-testid="edit-as-draft"
                  onClick={() => onEdit(version.id)}
                >
                  <FilePen className="size-3.5" aria-hidden="true" />
                  Edit as draft
                </Button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </SectionCard>
  );
}
