import { Coffee, Layers, Plus } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import type { RecipeRow } from "@/api/types";
import { EmptyState } from "@/components/layout/EmptyState";
import { PageHeader } from "@/components/layout/PageHeader";
import { ContinueDesigning, DesigningBadge } from "@/components/recipes/Designing";
import { NewRecipeDialog } from "@/components/recipes/NewRecipeDialog";
import { RecipesExplainer } from "@/components/recipes/RecipesExplainer";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useQueryErrorToast } from "@/hooks/useQueryErrorToast";
import { useRecipes, useSetAutomatch } from "@/hooks/useRecipes";
import { recipeSummary } from "@/lib/recipes";

/**
 * Every Recipe, the ones collecting shots first.
 *
 * A Recipe is one bean dialled in on one grinder with a profile of its own, so
 * the page explains the idea under its header (`RecipesExplainer`) rather than
 * in a subtitle's line. A card is the identity plus the two facts you act on:
 * how many shots it has collected, and whether new shots on its profile are
 * filed under it. Any number of Recipes may be — several grinders means several
 * beans loaded at once — and the matcher tells them apart by the profile a
 * shot was brewed with.
 *
 * A Recipe still being designed has no recipe to summarise, so its card says so
 * with a badge and offers the way back into the conversation instead.
 */
export function RecipesPage() {
  const [showArchived, setShowArchived] = useState(false);
  const recipes = useRecipes(showArchived);
  const [dialogOpen, setDialogOpen] = useState(false);
  const navigate = useNavigate();
  useQueryErrorToast(recipes.error, "Could not load the Recipes");

  const rows = recipes.data?.items ?? [];

  return (
    <div className="space-y-4">
      <PageHeader
        title="Recipes"
        subtitle="One bean on one grinder with its own profile, dialled in one version at a time."
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
            <Button size="sm" onClick={() => setDialogOpen(true)}>
              <Plus className="size-3.5" aria-hidden="true" />
              New Recipe
            </Button>
          </div>
        }
      />

      <NewRecipeDialog
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        onCreated={(recipeId) => navigate(`/recipes/${recipeId}`)}
        // An accepted starting point whose option carried a whole profile left
        // a draft staged on the profiles page, and that is the more urgent of
        // the two places to be: the Recipe is fine to look at later, the profile
        // is waiting for somebody to approve it before the machine has it at
        // all.
        onDraftCreated={() => navigate("/profiles#staged")}
      />

      {recipes.isPending ? null : <RecipesExplainer dialling={rows.some((row) => !row.archived)} />}

      {recipes.isPending ? (
        <div className="grid gap-3 md:grid-cols-2">
          <Skeleton className="h-28 w-full" />
          <Skeleton className="h-28 w-full" />
        </div>
      ) : rows.length === 0 ? (
        <EmptyState
          icon={Layers}
          title="No Recipes yet"
          description="Until a Recipe exists, every shot lands in the “needs a Recipe” inbox, because the archive can’t tell which bean was in the hopper. Start a Recipe and the next shot brewed on its profile files itself."
          action={
            <Button size="sm" onClick={() => setDialogOpen(true)}>
              New Recipe
            </Button>
          }
        />
      ) : (
        <div className="grid gap-3 md:grid-cols-2" data-testid="recipe-list">
          {rows.map((row) => (
            <RecipeCard key={row.id} row={row} />
          ))}
        </div>
      )}
    </div>
  );
}

function RecipeCard({ row }: { row: RecipeRow }) {
  const automatch = useSetAutomatch();

  return (
    <Card data-testid="recipe-card" data-recipe={row.id} className="gap-3">
      <CardHeader className="gap-1">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <CardTitle className="text-base">
            <Link to={`/recipes/${row.id}`} className="hover:underline">
              {row.name}
            </Link>
          </CardTitle>
          <div className="flex items-center gap-1">
            {row.designing ? <DesigningBadge /> : null}
            {/* Nothing to match on while designing: version 1 names no profile. */}
            {row.automatch && !row.designing ? (
              <Badge data-testid="recipe-automatch" className="gap-1">
                automatch
              </Badge>
            ) : null}
            {row.archived ? <Badge variant="outline">archived</Badge> : null}
          </div>
        </div>
        {row.designing ? (
          <p className="text-muted-foreground text-sm" data-testid="recipe-design-summary">
            {[row.bean_name ?? `bean #${row.bean_id}`, row.grinder_name]
              .filter(Boolean)
              .join(" · ")}{" "}
            · no version yet
          </p>
        ) : (
          <p className="text-muted-foreground text-sm">{recipeSummary(row)}</p>
        )}
      </CardHeader>
      <CardContent className="space-y-2">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
          <span className="tabular-nums">
            {row.shot_count} shot{row.shot_count === 1 ? "" : "s"}
          </span>
          <span className="text-muted-foreground">
            {row.version_count} version{row.version_count === 1 ? "" : "s"}
          </span>
        </div>
        <div className="flex items-center gap-2">
          {row.designing ? <ContinueDesigning recipe={row} /> : null}
          <Button asChild variant="outline" size="sm">
            <Link to={`/recipes/${row.id}`}>Open</Link>
          </Button>
          {row.archived || row.designing ? null : (
            <Button
              variant="ghost"
              size="sm"
              disabled={automatch.isPending}
              onClick={() => automatch.mutate({ id: row.id, automatch: !row.automatch })}
            >
              <Coffee className="size-3.5" aria-hidden="true" />
              {row.automatch ? "Stop filing shots here" : "File matching shots here"}
            </Button>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
