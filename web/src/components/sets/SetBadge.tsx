import { Layers } from "lucide-react";
import { Link } from "react-router-dom";
import type { ShotSetBadge } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

/**
 * Which Set a shot belongs to, in the width of a table cell, as a link to it.
 *
 * Only for a shot that has one. A shot without a Set is waiting for an answer,
 * and the shots list renders `NeedsSetMenu` in this place: the same dashed
 * "needs a Set" words, as a button that gives it one.
 */
export function SetBadge({ badge, className }: { badge: ShotSetBadge; className?: string }) {
  return (
    <Badge
      variant="secondary"
      asChild
      data-testid="set-badge"
      data-state="assigned"
      className={cn("gap-1", className)}
    >
      <Link to={`/sets/${badge.set_id}`} title={`${badge.set_name}, version ${badge.version_no}`}>
        <Layers className="size-3" aria-hidden="true" />
        <span className="max-w-[9rem] truncate">{badge.set_name}</span>
        <span className="tabular-nums opacity-70">v{badge.version_no}</span>
      </Link>
    </Badge>
  );
}
