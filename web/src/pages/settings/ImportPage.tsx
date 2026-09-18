import { Link } from "react-router-dom";
import { PageHeader } from "@/components/layout/PageHeader";
import { SectionCard } from "@/components/layout/SectionCard";
import { Button } from "@/components/ui/button";
import type { SettingsPageInfo } from "@/lib/settingsPages";

/**
 * Where importing went. The drop zone on the shots page is the feature; this
 * page exists so somebody looking for "import" under Settings is told where it
 * is. Its one card starts open: a page holding a single closed box is a click
 * that tells nobody anything.
 */
export function ImportPage({ page }: { page: SettingsPageInfo }) {
  return (
    <div className="space-y-6">
      <PageHeader title={page.label} subtitle={page.description} />
      <SectionCard
        id="exports"
        collapsible
        defaultOpen
        title="Exports from the machine's web UI"
        description="Dropped on the strip at the top of the shots page."
        contentClassName="space-y-3"
      >
        <p className="text-muted-foreground text-sm">
          The machine keeps about 300 KB of history and drops the oldest shots when it runs short.
          An export saved before that happened is the only way those shots come back. Drop the files
          on the strip at the top of the shots page; it reports what each one did.
        </p>
        <Button asChild variant="outline">
          <Link to="/shots">Open the shots page</Link>
        </Button>
      </SectionCard>
    </div>
  );
}
