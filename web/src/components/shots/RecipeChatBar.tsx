import type { RecipeRow } from "@/api/types";
import { DiscussButton } from "@/components/chat/DiscussButton";
import { cn } from "@/lib/utils";

/**
 * The Recipes a person is brewing: not archived, with a version to talk about, and
 * not still being designed — a Recipe being designed already has its room, the
 * design conversation, and nothing brewed yet to judge. The same filter the
 * "needs a Recipe" menu offers, in the order the Recipes list serves them.
 */
export function activeRecipes(recipes: RecipeRow[]): RecipeRow[] {
  return recipes.filter((row) => !row.archived && !row.designing && row.current_version_id != null);
}

/**
 * What the composer holds when a bar button lands on the Chat page. Generic on
 * purpose: the person has just judged the shots in the table, and the version's
 * conversation opens with its newest shots and their judgements already in
 * front of the model, so pressing Enter is enough. It is typed, not sent, so it
 * can be replaced with a question of their own.
 */
export function setChatQuestion(versionLabel: string): string {
  return `I've judged my latest shots on ${versionLabel}. What do they show, and what should I change next?`;
}

/**
 * One button per active Recipe above the shots table, each into the conversation
 * about that Recipe's current version — opened or continued, one per change, as
 * Discuss on the Recipe page does. The table is where shots are judged, so this is
 * the step after judging without a detour through a Recipe page.
 *
 * Nothing is drawn when no Recipe is active: a bar with no buttons is only a
 * sentence about an absence the Recipes page explains better.
 */
export function RecipeChatBar({
  recipes,
  className,
}: {
  recipes: RecipeRow[];
  className?: string;
}) {
  const active = activeRecipes(recipes);
  if (active.length === 0) return null;
  return (
    <nav
      aria-label="Chat about a Recipe"
      data-testid="recipe-chat-bar"
      className={cn("flex flex-wrap items-center gap-2", className)}
    >
      <span className="text-muted-foreground text-xs">Chat about</span>
      {active.map((row) => (
        <DiscussButton
          key={row.id}
          recipeId={row.id}
          versionId={row.current_version_id}
          label={`${row.name} · ${row.current_version_label}`}
          question={setChatQuestion(row.current_version_label)}
        />
      ))}
    </nav>
  );
}
