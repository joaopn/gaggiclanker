import { useState } from "react";
import { PageHeader } from "@/components/layout/PageHeader";
import { SectionCard } from "@/components/layout/SectionCard";
import { Button } from "@/components/ui/button";
import { useDownloadBackup } from "@/hooks/useBackup";
import { useHealth } from "@/hooks/useHealth";
import type { SettingsPageInfo } from "@/lib/settingsPages";
import { RestoreSection } from "@/pages/settings/RestoreSection";
import { useOpenGroups } from "@/pages/settings/useOpenGroups";

/** What the backend reports about itself, and the whole app as one downloadable file. */
export function SystemPage({ page }: { page: SettingsPageInfo }) {
  const health = useHealth();
  const backup = useDownloadBackup();
  const [includeKeys, setIncludeKeys] = useState(false);
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
        title="Backup & restore"
        description="The whole app in one file, and putting one back."
        contentClassName="space-y-6"
      >
        <div className="space-y-2">
          <h3 className="font-medium text-sm">Back up</h3>
          <p className="text-muted-foreground text-sm">
            Everything in the app in one file: shots, Sets, beans, profiles, chats, knowledge and
            settings.
          </p>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={includeKeys}
              onChange={(event) => setIncludeKeys(event.target.checked)}
            />
            Include API keys and tokens
          </label>
          {includeKeys && (
            <p className="text-muted-foreground text-xs">
              The file will hold your API keys and tokens in plain text. Keep it somewhere safe.
            </p>
          )}
          <Button
            variant="outline"
            onClick={() => backup.mutate(includeKeys)}
            disabled={backup.isPending}
            type="button"
          >
            {backup.isPending ? "Preparing..." : "Download backup"}
          </Button>
        </div>
        <RestoreSection />
      </SectionCard>
    </div>
  );
}
