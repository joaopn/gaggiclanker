import { GitBranch, Layers } from "lucide-react";
import { useEffect, useId, useState } from "react";
import { Link } from "react-router-dom";
import type { RecipeRow, RecipeVersionRow, ShotJudgement } from "@/api/types";
import { SectionCard } from "@/components/layout/SectionCard";
import { Button } from "@/components/ui/button";
import { useAddRecipeVersion, useAssignShot, useRecipe, useRecipes } from "@/hooks/useRecipes";
import { attempt } from "@/lib/mutations";
import { grindPatch, versionSummary } from "@/lib/recipes";
import { cn } from "@/lib/utils";

/**
 * Which Recipe this shot belongs to, and the shortcut for "that was a new version".
 *
 * The archive files a shot on its own when the Recipe collecting shots brews
 * its profile, so most of the time this panel is a confirmation. It exists for
 * the times it is not: a shot pulled on a profile no Recipe has, a shot pulled on
 * the wrong profile, a backfill from before any Recipe existed. Assigning by hand
 * overwrites, and the matcher never touches a shot that already has a version
 * — that asymmetry is what makes a correction stick.
 */

const FIELD = cn(
  "h-8 w-full rounded-md border border-input bg-background px-2 text-sm",
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
);

export function AssignToRecipe({
  shotId,
  recipeVersion,
  judgement,
}: {
  shotId: number;
  recipeVersion: RecipeVersionRow | null | undefined;
  judgement: ShotJudgement | null | undefined;
}) {
  const recipes = useRecipes();
  const assign = useAssignShot();
  const addVersion = useAddRecipeVersion();
  const [choice, setChoice] = useState<string>("");
  const [branching, setBranching] = useState(false);
  const [intent, setIntent] = useState("");
  // The grind typed into the branch form; `null` until the person touches it, so the field shows
  // the current version's recipe grind and a Recipe that moves under the form moves it too.
  const [grindInput, setGrindInput] = useState<string | null>(null);
  const ids = { recipe: useId(), intent: useId(), grind: useId() };

  const rows = recipes.data?.items ?? [];
  // Nothing is preselected for a shot with no Recipe. The matcher already files
  // every shot whose profile names one Recipe, so what is left here is the case
  // where the archive could not tell — several bags are loaded at once and only
  // the person knows which one this was.
  const preselected = recipeVersion ? String(recipeVersion.id) : "";

  // The preselection is derived state, so it re-runs when the derivation moves:
  // a shot that was just assigned under this page.
  useEffect(() => setChoice(preselected), [preselected]);

  const assignedRecipe = rows.find((row) => row.id === recipeVersion?.recipe_id);
  const chosenRecipe = rows.find((row) => String(row.current_version_id) === choice);
  const branchTarget = assignedRecipe ?? chosenRecipe;
  const dirty = choice !== preselected;
  // The grind the new version starts from: the Recipe's current version's recipe grind. A grind
  // belongs to the Recipe version, so it is set here and not in the judgement.
  const target = useRecipe(branching ? branchTarget?.id : undefined);
  const parent = target.data?.versions.find(
    (entry) => entry.version.id === target.data?.recipe.current_version_id,
  )?.version;
  const recipeGrind = parent?.grind_setting ?? "";
  const grind = grindInput ?? recipeGrind;

  return (
    <SectionCard
      title="Recipe"
      description="What you were brewing. The archive files a shot under the Recipe that brews its profile; anything else — a profile no Recipe has — waits here for an answer."
      actions={
        recipeVersion ? (
          <Link
            to={`/recipes/${recipeVersion.recipe_id}`}
            className="text-muted-foreground text-xs underline underline-offset-2"
          >
            Open the Recipe
          </Link>
        ) : null
      }
    >
      <div className="space-y-3" data-testid="assign-to-recipe">
        <p className="text-sm" data-testid="assign-current">
          {recipeVersion ? (
            <>
              <Layers className="mr-1 inline size-3.5 align-[-2px]" aria-hidden="true" />
              <span className="font-medium">
                {assignedRecipe?.name ?? "this Recipe"} {recipeVersion.version_label}
              </span>
              <span className="text-muted-foreground"> — {versionSummary(recipeVersion)}</span>
            </>
          ) : (
            <span className="text-muted-foreground">
              Not in a Recipe yet. Until it is, this shot is invisible to every trend and to the
              Recipe's conversations about what you have been trying.
            </span>
          )}
        </p>

        <div className="flex flex-wrap items-end gap-2">
          <div className="min-w-[16rem] flex-1">
            <label htmlFor={ids.recipe} className="mb-1 block text-muted-foreground text-xs">
              Assign to
            </label>
            <select
              id={ids.recipe}
              className={FIELD}
              value={choice}
              onChange={(event) => setChoice(event.target.value)}
            >
              <option value="">Not in a Recipe</option>
              {rows.map((row) => (
                <RecipeOption key={row.id} row={row} />
              ))}
              {/* A shot filed under an older version of a Recipe: the list above
                  only offers each Recipe's *current* version, so without this the
                  select would silently show the wrong thing. */}
              {recipeVersion && !rows.some((row) => row.current_version_id === recipeVersion.id) ? (
                <option value={String(recipeVersion.id)}>
                  {assignedRecipe?.name ?? "This Recipe"} — {recipeVersion.version_label} (where it
                  is now)
                </option>
              ) : null}
            </select>
          </div>
          <Button
            size="sm"
            disabled={!dirty || assign.isPending}
            onClick={() =>
              assign.mutate({
                shotId,
                recipeVersionId: choice === "" ? null : Number(choice),
              })
            }
          >
            {assign.isPending ? "Saving…" : "Assign"}
          </Button>
          {branchTarget ? (
            <Button
              size="sm"
              variant="outline"
              onClick={() => setBranching((open) => !open)}
              aria-expanded={branching}
            >
              <GitBranch className="size-3.5" aria-hidden="true" />
              New version from this shot
            </Button>
          ) : null}
        </div>

        {branching && branchTarget ? (
          <div
            className="space-y-2 rounded-md border border-border bg-muted/30 p-3"
            data-testid="branch-form"
          >
            <p className="text-muted-foreground text-xs">
              Records what you actually pulled — the doses from your judgement and the grind you set
              here — as{" "}
              {/* A grind, dose or yield change is a minor version by the shared
                  rule, and this form sends nothing else. */}
              {branchTarget.next_minor_label} of {branchTarget.name}, and files this shot under it.
            </p>
            <CopiedValues judgement={judgement} />
            <div>
              <label htmlFor={ids.grind} className="mb-1 block text-muted-foreground text-xs">
                Grind
              </label>
              <input
                id={ids.grind}
                className={FIELD}
                inputMode="decimal"
                placeholder="22, or 3.5"
                value={grind}
                onChange={(event) => setGrindInput(event.target.value)}
              />
            </div>
            <div>
              <label htmlFor={ids.intent} className="mb-1 block text-muted-foreground text-xs">
                What are you trying?
              </label>
              <input
                id={ids.intent}
                className={FIELD}
                placeholder="one click finer, chasing the sourness out"
                value={intent}
                onChange={(event) => setIntent(event.target.value)}
              />
            </div>
            <Button
              size="sm"
              disabled={addVersion.isPending || assign.isPending}
              onClick={async () => {
                const version = await attempt(() =>
                  addVersion.mutateAsync({
                    recipeId: branchTarget.id,
                    patch: {
                      intent,
                      // Recording what was actually pulled, after the fact: there is
                      // nothing left to predict about it.
                      prediction: "",
                      origin: "manual",
                      // Only the fields the judgement actually carries are sent:
                      // omitting one inherits the parent's value, and sending an
                      // empty one would record "cleared" as a change.
                      // The grind in the field (the recipe's own when left as shown), with its
                      // number, through the shared helper; emptied, it inherits the parent's.
                      ...grindPatch(grind),
                      ...(judgement?.dose_in_g ? { dose_g: judgement.dose_in_g } : {}),
                      ...(judgement?.dose_out_g ? { target_yield_g: judgement.dose_out_g } : {}),
                    },
                  }),
                );
                // Two writes, and the second only happens if the first did: a
                // shot filed under a version that was never created would be a
                // dangling reference the server would refuse anyway.
                if (!version) return;
                await attempt(() => assign.mutateAsync({ shotId, recipeVersionId: version.id }));
                setBranching(false);
                setIntent("");
                setGrindInput(null);
              }}
            >
              Create version
            </Button>
          </div>
        ) : null}
      </div>
    </SectionCard>
  );
}

function RecipeOption({ row }: { row: RecipeRow }) {
  return (
    <option value={row.current_version_id ? String(row.current_version_id) : ""}>
      {row.name} — {row.current_version_label}
      {row.automatch ? " (automatch)" : ""}
    </option>
  );
}

function CopiedValues({ judgement }: { judgement: ShotJudgement | null | undefined }) {
  const parts = [
    judgement?.dose_in_g ? `${judgement.dose_in_g} g in` : null,
    judgement?.dose_out_g ? `${judgement.dose_out_g} g out` : null,
  ].filter(Boolean);
  return (
    <p className="text-xs" data-testid="branch-copied">
      {parts.length > 0 ? (
        <>Copying: {parts.join(" · ")}</>
      ) : (
        <span className="text-muted-foreground">
          Your judgement records no doses, so the new version inherits the old version's numbers
          unchanged. Fill those in first if you changed something.
        </span>
      )}
    </p>
  );
}
