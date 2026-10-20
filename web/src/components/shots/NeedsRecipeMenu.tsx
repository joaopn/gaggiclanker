import { ChevronDown } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import type { RecipeRow, ShotListRow } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { useAssignShot, useRecipes } from "@/hooks/useRecipes";
import { attempt } from "@/lib/mutations";
import { ASSIGN_ANCHOR } from "@/lib/shots";
import { cn } from "@/lib/utils";

/**
 * The "needs a Recipe" badge, as the way to give the shot one.
 *
 * The inbox is where unfiled shots are seen, so the answer belongs where the
 * question is asked rather than one page away. Most of the time the answer is
 * one of the Recipes somebody is actively brewing, which is why the menu offers
 * only a few of them: the Assign panel on the shot page is the full list, and
 * this is the shortcut to its top.
 */

/**
 * How many Recipes the menu offers. The server lists the ones collecting shots
 * first and then the newest, so three covers the bags in the hoppers and the
 * couple before them; a longer list is a select box, and the shot page already
 * has one.
 */
export const MENU_RECIPES = 3;

export function NeedsRecipeMenu({ shot, className }: { shot: ShotListRow; className?: string }) {
  const [open, setOpen] = useState(false);
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Badge
          asChild
          variant="outline"
          className={cn(
            "max-w-full cursor-pointer border-dashed text-muted-foreground hover:bg-muted hover:text-foreground",
            "focus-visible:outline-none",
            className,
          )}
        >
          <button
            type="button"
            data-testid="recipe-badge"
            data-state="needs-recipe"
            title="This shot is not attached to a Recipe yet"
            // The visible words stay the start of the name, so a voice user who
            // says what they see still reaches the button; the rest says what
            // pressing it does, and for which row of a list of identical badges.
            aria-label={`needs a Recipe: choose one for shot ${shot.device_id}`}
          >
            {/* Truncates like an assigned badge when the Recipe column is narrow. */}
            <span className="min-w-0 truncate">needs a Recipe</span>
            <ChevronDown className="size-3 shrink-0" aria-hidden="true" />
          </button>
        </Badge>
      </PopoverTrigger>
      <PopoverContent
        anchored
        align="start"
        className="w-72 space-y-2 p-2"
        data-testid="needs-recipe-menu"
        aria-label={`File shot ${shot.device_id} under a Recipe`}
      >
        <MenuBody shot={shot} onDone={() => setOpen(false)} />
      </PopoverContent>
    </Popover>
  );
}

/**
 * Split out so the Recipe list is not asked for until the menu is open: an inbox
 * of fifty unfiled rows must not be fifty subscriptions to it.
 */
function MenuBody({ shot, onDone }: { shot: ShotListRow; onDone: () => void }) {
  const recipes = useRecipes();
  const assign = useAssignShot();
  const choicesRef = useRef<HTMLFieldSetElement>(null);
  const loaded = recipes.isSuccess;

  // The popover focuses its first focusable child once it is placed, and on a
  // first open that is before the Recipes have arrived — so focus sits on the
  // panel itself. When the choices land, move it onto the first of them, but
  // only if it is still on the panel: someone who has already tabbed or
  // clicked elsewhere keeps their place. With the list cached the choices are
  // there on the first commit and the popover's own focus handles it.
  useEffect(() => {
    if (!loaded) return;
    const panel = choicesRef.current?.closest<HTMLElement>('[role="dialog"]');
    if (!panel || document.activeElement !== panel) return;
    choicesRef.current?.querySelector<HTMLElement>("button")?.focus({ preventScroll: true });
  }, [loaded]);

  if (recipes.isPending) {
    return <StateLine>Loading Recipes…</StateLine>;
  }
  if (recipes.isError) {
    return <StateLine>Could not load the Recipes: {recipes.error.message}</StateLine>;
  }

  // Only a Recipe with a version can take a shot — the assignment names a version,
  // not a Recipe — and `useRecipes()` already leaves archived ones out; the archived
  // check is belt and braces for a list that was fetched with them in. A Recipe
  // the matcher is not offered is still offered here: picking one by hand is
  // exactly what the flag being off leaves to the person.
  const candidates = recipes.data.items.filter(
    (row) => !row.archived && row.current_version_id != null,
  );
  const offered = candidates.slice(0, MENU_RECIPES);

  if (offered.length === 0) {
    return (
      <div className="space-y-2 p-2 text-sm" data-testid="needs-recipe-empty">
        <p className="text-muted-foreground">
          No Recipes yet. A Recipe is the bean, grinder and profile a shot was an attempt at.
        </p>
        <Link to="/recipes" className="text-primary text-sm underline underline-offset-2">
          Start a Recipe
        </Link>
      </div>
    );
  }

  async function choose(row: RecipeRow) {
    // One write at a time. `aria-disabled` rather than `disabled` below: a
    // disabled button drops focus to the page in Chromium, and a keyboard user
    // whose assignment failed would be left outside the menu they are in.
    if (assign.isPending) return;
    // The latest version, always: that is the recipe being brewed now. Filing
    // under an older one is a correction, and the shot page is where that is
    // made. The menu closes only on success; a failure has already toasted
    // from the hook, and the choices stay there to try again.
    const assigned = await attempt(() =>
      assign.mutateAsync({ shotId: shot.id, recipeVersionId: row.current_version_id ?? null }),
    );
    if (assigned !== undefined) onDone();
  }

  return (
    <div className="space-y-1">
      {/* A fieldset because the choices are one question, and its legend is
          what a screen reader announces on entering them. */}
      <fieldset ref={choicesRef} className="m-0 min-w-0 space-y-0.5 border-0 p-0">
        <legend className="px-2 pt-1 text-muted-foreground text-xs">File under</legend>
        {offered.map((row) => (
          <button
            key={row.id}
            type="button"
            data-testid="needs-recipe-option"
            data-recipe={row.id}
            aria-disabled={assign.isPending}
            onClick={() => void choose(row)}
            className={cn(
              "block w-full rounded-md px-2 py-1.5 text-left",
              "hover:bg-muted focus-visible:bg-muted focus-visible:outline-none aria-disabled:opacity-60",
            )}
          >
            <span className="flex items-center gap-1.5 text-sm">
              <span className="min-w-0 truncate font-medium">{row.name}</span>
              <span className="text-muted-foreground tabular-nums">
                {row.current_version_label}
              </span>
              {row.automatch ? (
                <Badge variant="secondary" className="ml-auto">
                  automatch
                </Badge>
              ) : null}
            </span>
            <span className="block truncate text-muted-foreground text-xs">{identity(row)}</span>
          </button>
        ))}
      </fieldset>
      {candidates.length > offered.length ? (
        <Link
          to={`/shots/${shot.id}#${ASSIGN_ANCHOR}`}
          data-testid="needs-recipe-more"
          className="block rounded-md px-2 py-1.5 text-muted-foreground text-xs hover:bg-muted hover:text-foreground"
        >
          Another Recipe…
        </Link>
      ) : null}
    </div>
  );
}

/**
 * What the Recipe is, from the fields the list row already carries. Not the
 * recipe (`versionSummary`): the list does not include versions, and a request
 * per offered Recipe to fetch one would make opening a three-line menu three
 * round trips.
 */
function identity(row: RecipeRow): string {
  return [row.bean_name ?? `bean #${row.bean_id}`, row.grinder_name, row.profile_label]
    .filter(Boolean)
    .join(" · ");
}

function StateLine({ children }: { children: React.ReactNode }) {
  return (
    <p className="p-2 text-muted-foreground text-sm" data-testid="needs-recipe-state">
      {children}
    </p>
  );
}
