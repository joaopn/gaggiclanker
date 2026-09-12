import { Coffee, Layers, Plus } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import type { SetRow } from "@/api/types";
import { EmptyState } from "@/components/layout/EmptyState";
import { PageHeader } from "@/components/layout/PageHeader";
import { NewSetWizard } from "@/components/sets/NewSetWizard";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useQueryErrorToast } from "@/hooks/useQueryErrorToast";
import { useActivateSet, useSets } from "@/hooks/useSets";
import { daysOffRoast, freshness, setSummary } from "@/lib/sets";
import { TONE_TEXT } from "@/lib/shots";
import { cn } from "@/lib/utils";

/**
 * Every Set, the active one first.
 *
 * A card is the identity plus the two facts you act on: how many shots it has
 * collected, and whether it is the one the machine is set up for right now.
 * That flag is what auto-assignment consults, so it is the difference between
 * "the next shot files itself" and "the next shot waits in the inbox".
 */
export function SetsPage() {
  const [showArchived, setShowArchived] = useState(false);
  const sets = useSets(showArchived);
  const [wizardOpen, setWizardOpen] = useState(false);
  const navigate = useNavigate();
  useQueryErrorToast(sets.error, "Could not load the Sets");

  const rows = sets.data?.items ?? [];

  return (
    <div className="space-y-4">
      <PageHeader
        title="Sets"
        subtitle="One bag, one machine, one grinder — and every recipe you have tried with it."
        actions={
          <div className="flex items-center gap-2">
            <Button
              variant="ghost"
              size="sm"
              aria-pressed={showArchived}
              onClick={() => setShowArchived((current) => !current)}
            >
              {showArchived ? "Hide archived" : "Show archived"}
            </Button>
            <Button size="sm" onClick={() => setWizardOpen(true)}>
              <Plus className="size-3.5" aria-hidden="true" />
              New Set
            </Button>
          </div>
        }
      />

      <NewSetWizard
        open={wizardOpen}
        onOpenChange={setWizardOpen}
        onCreated={(setId) => navigate(`/sets/${setId}`)}
      />

      {sets.isPending ? (
        <div className="grid gap-3 md:grid-cols-2">
          <Skeleton className="h-28 w-full" />
          <Skeleton className="h-28 w-full" />
        </div>
      ) : rows.length === 0 ? (
        <EmptyState
          icon={Layers}
          title="No Sets yet"
          description="Until a Set exists, every shot lands in the “needs a Set” inbox: the archive has no way to know which bag was in the hopper. Start one and the next shot files itself."
          action={
            <Button size="sm" onClick={() => setWizardOpen(true)}>
              New Set
            </Button>
          }
        />
      ) : (
        <div className="grid gap-3 md:grid-cols-2" data-testid="set-list">
          {rows.map((row) => (
            <SetCard key={row.id} row={row} />
          ))}
        </div>
      )}
    </div>
  );
}

function SetCard({ row }: { row: SetRow }) {
  const activate = useActivateSet();
  const fresh = freshness(daysOffRoast(row.bean_roast_date));

  return (
    <Card data-testid="set-card" data-set={row.id} className="gap-3">
      <CardHeader className="gap-1">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <CardTitle className="text-base">
            <Link to={`/sets/${row.id}`} className="hover:underline">
              {row.name}
            </Link>
          </CardTitle>
          <div className="flex items-center gap-1">
            {row.active ? (
              <Badge data-testid="set-active" className="gap-1">
                active
              </Badge>
            ) : null}
            {row.status === "archived" ? <Badge variant="outline">archived</Badge> : null}
          </div>
        </div>
        <p className="text-muted-foreground text-sm">{setSummary(row)}</p>
      </CardHeader>
      <CardContent className="space-y-2">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
          <span className="tabular-nums">
            {row.shot_count} shot{row.shot_count === 1 ? "" : "s"}
          </span>
          <span className="text-muted-foreground">
            {row.version_count} version{row.version_count === 1 ? "" : "s"}
          </span>
          {row.bean_roast_date ? (
            <span className={cn("text-xs", TONE_TEXT[fresh.tone])} title={fresh.meaning}>
              {fresh.label}
            </span>
          ) : null}
        </div>
        <div className="flex items-center gap-2">
          <Button asChild variant="outline" size="sm">
            <Link to={`/sets/${row.id}`}>Open</Link>
          </Button>
          {!row.active && row.status === "active" ? (
            <Button
              variant="ghost"
              size="sm"
              disabled={activate.isPending}
              onClick={() => activate.mutate(row.id)}
            >
              <Coffee className="size-3.5" aria-hidden="true" />
              This is what is loaded
            </Button>
          ) : null}
        </div>
      </CardContent>
    </Card>
  );
}
