import { SlidersHorizontal, Star } from "lucide-react";
import { EmptyState } from "@/components/layout/EmptyState";
import { PageHeader } from "@/components/layout/PageHeader";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { useProfiles } from "@/hooks/useArchive";
import { useQueryErrorToast } from "@/hooks/useQueryErrorToast";

/**
 * The profile mirror, read-only — and it stays read-only for now.
 *
 * A profile with zero phases crashes brew start on the display, and a float
 * `pump: 100.0` is parsed as an object with zero targets. Writing to the
 * machine is a feature with its own safety work; reading is this.
 */
export function ProfilesPage() {
  const profiles = useProfiles();

  useQueryErrorToast(profiles.error, "Could not load profiles");

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
    </div>
  );
}
