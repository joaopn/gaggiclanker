import { PageHeader } from "@/components/layout/PageHeader";
import { SectionCard } from "@/components/layout/SectionCard";
import { Button } from "@/components/ui/button";
import { useHealth } from "@/hooks/useHealth";
import { useCreateBackup } from "@/hooks/useSettings";
import type { SettingsPageInfo } from "@/lib/settingsPages";
import { useOpenGroups } from "@/pages/settings/useOpenGroups";

/** What the backend reports about itself, and a copy of the database on demand. */
export function SystemPage({ page }: { page: SettingsPageInfo }) {
  const health = useHealth();
  const backup = useCreateBackup();
  const { isOpen, setGroupOpen } = useOpenGroups();

  return (
    <div className="space-y-6">
      <PageHeader title={page.label} subtitle={page.description} />

      <SectionCard
        id="status"
        collapsible
        open={isOpen("status")}
        onOpenChange={(open) => setGroupOpen("status", open)}
        title="Status"
        description="The backend's own health check: its version and whether the database answers."
      >
        <dl className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-3">
          <div>
            <dt className="text-muted-foreground text-xs">Status</dt>
            <dd data-testid="health-status">
              {health.data?.status ?? (health.isError ? "unreachable" : "...")}
            </dd>
          </div>
          <div>
            <dt className="text-muted-foreground text-xs">Version</dt>
            <dd data-testid="health-version">{health.data?.version ?? "-"}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground text-xs">Database</dt>
            <dd data-testid="health-database">{health.data?.database ?? "-"}</dd>
          </div>
        </dl>
      </SectionCard>

      <SectionCard
        id="backup"
        collapsible
        open={isOpen("backup")}
        onOpenChange={(open) => setGroupOpen("backup", open)}
        title="Backup"
        description="A copy of the database, written next to it under the data directory."
      >
        <div className="flex flex-wrap items-center gap-3">
          <Button
            variant="outline"
            onClick={() => backup.mutate()}
            disabled={backup.isPending}
            type="button"
          >
            {backup.isPending ? "Backing up..." : "Back up database"}
          </Button>
          <p className="text-muted-foreground text-xs">
            Written with <code>VACUUM INTO</code> under the data directory, so it lands on the host
            bind mount immediately.
          </p>
        </div>
      </SectionCard>
    </div>
  );
}
