import { Layers } from "lucide-react";
import { Link } from "react-router-dom";
import type { ShotRecipeBadge } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

/**
 * Which Recipe a shot belongs to, in the width of a table cell, as a link to it.
 *
 * Only for a shot that has one. A shot without a Recipe is waiting for an answer,
 * and the shots list renders `NeedsRecipeMenu` in this place: the same dashed
 * "needs a Recipe" words, as a button that gives it one.
 */
export function RecipeBadge({ badge, className }: { badge: ShotRecipeBadge; className?: string }) {
  return (
    <Badge
      variant="secondary"
      asChild
      data-testid="recipe-badge"
      data-state="assigned"
      // As wide as its name, up to whatever the column gives it: the name is
      // what gives way, down to an ellipsis, and the version and the icon
      // stay. The whole name is on hover.
      className={cn("max-w-full gap-1", className)}
    >
      <Link
        to={`/recipes/${badge.recipe_id}`}
        title={`${badge.recipe_name}, ${badge.version_label}`}
      >
        <Layers className="size-3 shrink-0" aria-hidden="true" />
        <span className="min-w-0 truncate">{badge.recipe_name}</span>
        <span className="shrink-0 tabular-nums opacity-70">{badge.version_label}</span>
      </Link>
    </Badge>
  );
}
