import { Layers } from "lucide-react";
import { Link } from "react-router-dom";
import type { ShotSetBadge } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

/**
 * Which Set a shot belongs to, in the width of a table cell.
 *
 * `null` is a state, not a gap: a shot the archive could not attach to a Set is
 * waiting for an answer, and saying "needs a Set" is what turns it into
 * something a reader can act on. Rendering nothing would make the whole inbox
 * invisible.
 *
 * This null rendering is the inert one. The shots list renders
 * `NeedsSetMenu` in its place, where the same words are a button that files
 * the shot.
 */
export function SetBadge({ badge, className }: { badge: ShotSetBadge | null; className?: string }) {
  if (!badge) {
    return (
      <Badge
        variant="outline"
        data-testid="set-badge"
        data-state="needs-set"
        title="This shot is not attached to a Set yet"
        className={cn("border-dashed text-muted-foreground", className)}
      >
        needs a Set
      </Badge>
    );
  }
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
