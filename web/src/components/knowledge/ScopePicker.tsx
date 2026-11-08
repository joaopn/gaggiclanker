import { useId } from "react";
import type { KnowledgeInsightScope, ScopeOption, ScopeOptionsData } from "@/api/types";
import { useScopeOptions, useScopeReach } from "@/hooks/useKnowledge";

const FIELD =
  "h-9 w-full rounded-md border border-input bg-background px-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";

/** The keys a person can narrow a general insight by, in the order the server states them. */
export const PICKER_KEYS = ["bean_id", "roast_level", "process", "origin", "grinder_id"] as const;
type PickerKey = (typeof PICKER_KEYS)[number];

/** A scope with only the keys it names: what is sent, and what the reach is asked about. */
export function statedScope(scope: KnowledgeInsightScope | undefined): KnowledgeInsightScope {
  const stated: Record<string, unknown> = {};
  for (const key of PICKER_KEYS) {
    const value = scope?.[key];
    if (value !== undefined && value !== null && value !== "") stated[key] = value;
  }
  return stated as KnowledgeInsightScope;
}

function sameValue(a: unknown, b: unknown): boolean {
  if (typeof a === "string" && typeof b === "string") {
    return a.trim().toLowerCase() === b.trim().toLowerCase();
  }
  return a === b;
}

/** The words for something a bean or grinder id names that no list offers. */
const GONE: Record<string, string> = {
  bean_id: "a bean not offered here any more",
  grinder_id: "a grinder not offered here any more",
};

/**
 * What one value of a scope is called, or `null` when nothing names it.
 *
 * A Recipe's own attributes name themselves (the options the server states for it); a bean and
 * a grinder are looked up in the archive's lists; failing that, `named` (the names the agent's
 * request carried, "bean Ethiopia Guji") says what an id was, so an archived bean is still
 * called by its name. Roast, process and origin are their own words.
 */
function nameOf(
  key: PickerKey,
  value: string | number,
  options: ScopeOptionsData | undefined,
  named: string[] | undefined,
): string | null {
  const own = options?.recipe?.find(
    (option) => option.key === key && sameValue(option.value, value),
  );
  if (own) return own.label;
  if (key === "bean_id" || key === "grinder_id") {
    const word = key === "bean_id" ? "bean" : "grinder";
    const list = key === "bean_id" ? options?.beans : options?.grinders;
    const found = list?.find((choice) => choice.id === value);
    if (found) return `${word} ${found.label}`;
    return named?.find((name) => name.startsWith(`${word} `)) ?? null;
  }
  return `${key === "roast_level" ? "roast" : key} ${String(value)}`;
}

/**
 * What a scope is called on screen: "grinder Niche Zero", never an id. A value nothing names is
 * said in words ("a bean not offered here any more") and never as an id.
 */
export function scopeLabels(
  scope: KnowledgeInsightScope,
  options?: ScopeOptionsData,
  named?: string[],
): string[] {
  const labels: string[] = [];
  for (const key of PICKER_KEYS) {
    const value = scope[key];
    if (value === undefined || value === null || value === "") continue;
    labels.push(nameOf(key, value, options, named) ?? GONE[key] ?? String(value));
  }
  return labels;
}

/** "4 Recipes", "1 Recipe". */
export function recipesWords(recipes: number): string {
  return `${recipes} ${recipes === 1 ? "Recipe" : "Recipes"}`;
}

/** "Matches 4 Recipes now." The one number that says how far an Add reaches. */
export function reachSentence(recipes: number): string {
  return `Matches ${recipesWords(recipes)} now.`;
}

/**
 * What a general insight applies to: one control for the card, "Save as general insight" and the
 * Knowledge page.
 *
 * With a Recipe (a Recipe or Design conversation) it is a checkbox for each thing that Recipe
 * states: its bean, grinder, roast, process and origin, by name. Without one (a General
 * conversation, the Knowledge page) it is a select for each, with "any". Underneath, in words,
 * what an empty choice means and, from the server, how many Recipes the scope matches right now,
 * read again on every change. No id is ever shown.
 */
export function ScopePicker({
  recipeId,
  value,
  onChange,
  disabled = false,
  named,
}: {
  /** The conversation's Recipe, or `null` for the archive's whole choice. */
  recipeId: number | null;
  value: KnowledgeInsightScope;
  onChange: (scope: KnowledgeInsightScope) => void;
  disabled?: boolean;
  /** The names the agent's request carried ("bean Ethiopia Guji"), for a value no list offers. */
  named?: string[];
}) {
  const options = useScopeOptions(recipeId);
  const stated = statedScope(value);
  const reach = useScopeReach(stated);
  const idBase = useId();
  // A value the scope already holds that the Recipe no longer states (its bean was edited, or an
  // older choice) stays a ticked box with its name, so what the card shows is exactly what Add
  // saves; unticking it drops it.
  const offered: { option: ScopeOption; stale: boolean }[] = (options.data?.recipe ?? []).map(
    (option) => ({ option, stale: false }),
  );
  if (options.data && recipeId !== null) {
    for (const key of PICKER_KEYS) {
      const held = stated[key];
      if (held === undefined || held === null) continue;
      if (offered.some(({ option }) => option.key === key && sameValue(option.value, held)))
        continue;
      const name = nameOf(key, held, options.data, named);
      offered.push({
        option: {
          key,
          value: held,
          label: name
            ? `${name} (not offered here any more)`
            : (GONE[key] ?? `${String(held)} (not offered here any more)`),
        },
        stale: true,
      });
    }
  }
  const chosen = Object.keys(stated).length > 0;

  /**
   * A value the scope holds that no option offers (a bean archived since, an older choice): the
   * select shows it as its own selected option, so what it shows is what Add saves.
   */
  function held(
    key: PickerKey,
    offeredValues: (string | number)[],
  ): { value: string; label: string } | null {
    const value = stated[key];
    if (!options.data || value === undefined || value === null) return null;
    if (offeredValues.some((offered) => sameValue(offered, value))) return null;
    const name = nameOf(key, value, options.data, named);
    return {
      value: String(value),
      label: name
        ? `${name} (not offered here any more)`
        : (GONE[key] ?? `${String(value)} (not offered here any more)`),
    };
  }

  /** The select's value: the offered option the stored one matches, in the option's own spelling. */
  function shown(key: PickerKey, offeredValues: (string | number)[]): string | number {
    const value = stated[key];
    if (value === undefined || value === null) return "";
    return offeredValues.find((offered) => sameValue(offered, value)) ?? value;
  }

  function set(key: PickerKey, next: string | number | null) {
    const scope: Record<string, unknown> = { ...stated };
    if (next === null || next === "") delete scope[key];
    else scope[key] = next;
    onChange(scope as KnowledgeInsightScope);
  }

  return (
    <div className="space-y-2" data-testid="scope-picker">
      {options.isError ? (
        <p role="alert" className="text-destructive text-sm">
          Could not read what this can apply to: {options.error.message}
        </p>
      ) : recipeId !== null ? (
        <fieldset className="space-y-1" disabled={disabled}>
          <legend className="text-muted-foreground text-xs">Applies to</legend>
          {offered.map(({ option, stale }) => {
            const key = option.key as PickerKey;
            const id = `${idBase}-${key}${stale ? "-held" : ""}`;
            return (
              <label key={id} htmlFor={id} className="flex items-center gap-2 text-sm">
                <input
                  id={id}
                  type="checkbox"
                  className="size-4"
                  checked={stale || sameValue(stated[key], option.value)}
                  onChange={(event) => set(key, event.target.checked ? option.value : null)}
                />
                <span>{option.label}</span>
              </label>
            );
          })}
        </fieldset>
      ) : (
        <fieldset className="grid gap-2 sm:grid-cols-2" disabled={disabled}>
          <legend className="mb-1 text-muted-foreground text-xs">Applies to</legend>
          <Choice
            id={`${idBase}-bean`}
            label="Bean"
            value={shown(
              "bean_id",
              (options.data?.beans ?? []).map((choice) => choice.id),
            )}
            held={held(
              "bean_id",
              (options.data?.beans ?? []).map((choice) => choice.id),
            )}
            onPick={(next) => set("bean_id", next === "" ? null : Number(next))}
            options={(options.data?.beans ?? []).map((bean) => ({
              value: String(bean.id),
              label: bean.label,
            }))}
          />
          <Choice
            id={`${idBase}-grinder`}
            label="Grinder"
            value={shown(
              "grinder_id",
              (options.data?.grinders ?? []).map((choice) => choice.id),
            )}
            held={held(
              "grinder_id",
              (options.data?.grinders ?? []).map((choice) => choice.id),
            )}
            onPick={(next) => set("grinder_id", next === "" ? null : Number(next))}
            options={(options.data?.grinders ?? []).map((grinder) => ({
              value: String(grinder.id),
              label: grinder.label,
            }))}
          />
          <Choice
            id={`${idBase}-roast`}
            label="Roast"
            value={shown(
              "roast_level",
              (options.data?.roast_levels ?? []).map((choice) => choice.value),
            )}
            held={held(
              "roast_level",
              (options.data?.roast_levels ?? []).map((term) => term.value),
            )}
            onPick={(next) => set("roast_level", next)}
            options={options.data?.roast_levels ?? []}
          />
          <Choice
            id={`${idBase}-process`}
            label="Process"
            value={shown(
              "process",
              (options.data?.processes ?? []).map((choice) => choice.value),
            )}
            held={held(
              "process",
              (options.data?.processes ?? []).map((term) => term.value),
            )}
            onPick={(next) => set("process", next)}
            options={options.data?.processes ?? []}
          />
          <Choice
            id={`${idBase}-origin`}
            label="Origin"
            value={shown(
              "origin",
              (options.data?.origins ?? []).map((choice) => choice.value),
            )}
            held={held(
              "origin",
              (options.data?.origins ?? []).map((term) => term.value),
            )}
            onPick={(next) => set("origin", next)}
            options={options.data?.origins ?? []}
          />
        </fieldset>
      )}
      <p className="text-muted-foreground text-xs" data-testid="scope-picker-summary">
        {chosen
          ? null
          : recipeId !== null
            ? "Nothing ticked: every Recipe. "
            : "Nothing chosen: every Recipe. "}
        {reach.data ? (
          <span data-testid="scope-reach">{reachSentence(reach.data.recipes)}</span>
        ) : null}
      </p>
    </div>
  );
}

function Choice({
  id,
  label,
  value,
  options,
  held,
  onPick,
}: {
  id: string;
  label: string;
  value: string | number;
  options: { value: string; label: string }[];
  held: { value: string; label: string } | null;
  onPick: (value: string) => void;
}) {
  return (
    <div className="space-y-1">
      <label htmlFor={id} className="text-muted-foreground text-xs">
        {label}
      </label>
      <select
        id={id}
        className={FIELD}
        value={String(value)}
        onChange={(event) => onPick(event.target.value)}
      >
        <option value="">any</option>
        {held ? <option value={held.value}>{held.label}</option> : null}
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </div>
  );
}
