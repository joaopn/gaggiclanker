/**
 * Names a stored conversation may still carry.
 *
 * Four tools had the old word in their names. A conversation is history: its stored tool calls and
 * results keep the names and the keys they were made under, so the cards built from them read
 * both. The same map as the back end's `tools/legacy.py`.
 */
export const LEGACY_TOOL_NAMES: Record<string, string> = {
  get_set: "get_recipe",
  list_sets: "list_recipes",
  list_set_shots: "list_recipe_shots",
  propose_set_version: "propose_recipe_version",
};

/** The tool name as it is today (unchanged for a tool that was never renamed). */
export function currentToolName(name: string): string {
  return LEGACY_TOOL_NAMES[name] ?? name;
}

/** The Recipe a tool output names: `recipe_id`, or `set_id` in the output of an old call. */
export function recipeIdOf(output: Record<string, unknown>): unknown {
  return output.recipe_id ?? output.set_id;
}
