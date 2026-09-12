import { AlertTriangle, FilePen } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { DraftCard } from "@/components/drafts/DraftCard";
import { EmptyState } from "@/components/layout/EmptyState";
import { PageHeader } from "@/components/layout/PageHeader";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useDeviceWrites } from "@/hooks/useDeviceStatus";
import { useProfileDrafts } from "@/hooks/useDrafts";
import { useQueryErrorToast } from "@/hooks/useQueryErrorToast";

/**
 * The draft queue: profiles proposed for the machine, and what stands in the way.
 *
 * `open` is the default view and it includes `failed` on purpose — a push that
 * did not verify left a profile on the display that somebody has to decide
 * about, and filing it under "done" is how it stays there for a month.
 *
 * The banner at the top is the switch. A person looking at a queue of approved
 * drafts that will not push needs to be told why in the place they are looking,
 * not on the Settings page they have not opened.
 */
export function DraftsPage() {
  const [showAll, setShowAll] = useState(false);
  const drafts = useProfileDrafts(showAll ? {} : { open: true });
  const writes = useDeviceWrites();

  useQueryErrorToast(drafts.error, "Could not load the draft queue");

  const items = drafts.data?.items ?? [];

  return (
    <div className="space-y-6">
      <PageHeader
        title="Profile drafts"
        subtitle="Proposed profiles, on their way to the machine. Every one is saved as a new profile with an [AI] suffix — nothing is ever overwritten, and nothing is ever selected for you."
        actions={
          <Button variant="outline" size="sm" onClick={() => setShowAll((open) => !open)}>
            {showAll ? "Show only what is open" : "Show everything"}
          </Button>
        }
      />

      {writes.data && !writes.data.enabled ? (
        <div
          className="rounded-md border border-status-warn/40 bg-status-warn/10 p-3"
          data-testid="writes-disabled-banner"
        >
          <p className="flex items-center gap-1.5 font-medium text-sm text-status-warn-text">
            <AlertTriangle className="size-3.5" aria-hidden="true" />
            Writing to the machine is switched off
          </p>
          <p className="mt-1 text-status-warn-text text-xs">
            Drafting and approving work; pushing is refused before anything reaches the wire, and
            the refusal is recorded in the write audit. Turn on "Device writes enabled" under{" "}
            <Link className="underline underline-offset-2" to="/settings">
              Settings → Machine
            </Link>
            .
          </p>
        </div>
      ) : null}

      {drafts.isPending ? (
        <div className="space-y-3" data-testid="drafts-skeleton">
          <Skeleton className="h-32 w-full" />
          <Skeleton className="h-32 w-full" />
        </div>
      ) : items.length === 0 ? (
        <EmptyState
          icon={FilePen}
          title={showAll ? "No drafts yet" : "Nothing waiting"}
          description={
            showAll
              ? "A draft comes from an analysis that suggested a profile change, or from editing a profile version by hand on the Profiles page."
              : "Everything drafted so far has been pushed, discarded or overtaken."
          }
          action={
            <Button asChild variant="outline">
              <Link to="/profiles">Open the profiles page</Link>
            </Button>
          }
        />
      ) : (
        <ul className="space-y-3" data-testid="draft-list">
          {items.map((draft) => (
            <DraftCard key={draft.id} draft={draft} />
          ))}
        </ul>
      )}
    </div>
  );
}
