import { MessageSquare } from "lucide-react";
import { Link } from "react-router-dom";
import type { RecipeRow } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

/**
 * A Recipe still being designed, marked the same way wherever a Recipe is listed.
 *
 * Such a Recipe has a bean, a grinder and a version 1 with no recipe; the recipe
 * is being worked out with the agent in version 1's conversation. The badge is
 * how an abandoned design stays visible — on the Recipes list, the Recipe page and
 * the Chat page's badges — rather than looking like a Recipe that somehow has
 * nothing in it.
 */
export function DesigningBadge() {
  return (
    <Badge variant="secondary" data-testid="recipe-designing">
      Designing
    </Badge>
  );
}

/**
 * Back into the design conversation.
 *
 * Through the Chat page's open-or-continue link for version 1 — the one Discuss
 * uses — so it lands in the conversation the design started in rather than a
 * new one. Nothing is prefilled: the person picks up where they left off.
 */
export function ContinueDesigning({
  recipe,
}: {
  recipe: Pick<RecipeRow, "id" | "current_version_id">;
}) {
  const params = new URLSearchParams({ recipe: String(recipe.id) });
  if (recipe.current_version_id !== null && recipe.current_version_id !== undefined) {
    params.set("version", String(recipe.current_version_id));
  }
  return (
    <Button asChild variant="outline" size="sm">
      <Link to={`/chat?${params.toString()}`} data-testid="continue-designing">
        <MessageSquare className="size-3.5" aria-hidden="true" />
        Continue designing
      </Link>
    </Button>
  );
}
