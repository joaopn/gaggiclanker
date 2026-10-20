import { createElement } from "react";
import { Navigate, useLocation } from "react-router-dom";

/**
 * Links, bookmarks and the browser's own memory from before a Set was called a Recipe.
 *
 * A link made then carries the Recipe as `set` in its query string and `#set` in its hash, points
 * at `/sets` and `/sets/<id>`, and a stored view names the Recipe column `set`. They are all read
 * as what they meant; nothing here writes the old words.
 */

/** The query parameter that named a Recipe before the rename. */
export const LEGACY_RECIPE_PARAM = "set";

/** The hash of the shot page's Assign panel before the rename. */
export const LEGACY_ASSIGN_ANCHOR = "set";

/**
 * The query string with an old `set=` moved to `recipe=`, or `null` when there is none to move.
 * A link that somehow carries both keeps its `recipe`: the new word wins.
 */
export function withRecipeParam(params: URLSearchParams): URLSearchParams | null {
  if (!params.has(LEGACY_RECIPE_PARAM)) return null;
  const next = new URLSearchParams(params);
  const old = next.get(LEGACY_RECIPE_PARAM);
  next.delete(LEGACY_RECIPE_PARAM);
  if (!next.has("recipe") && old !== null) next.set("recipe", old);
  return next;
}

/** `/sets` and `/sets/<id>…`, as `/recipes…`: the path tail, the query and the hash kept. */
export function recipesPathFor(pathname: string): string {
  return pathname.replace(/^\/sets(?=\/|$)/, "/recipes");
}

/** The route behind `/sets` and `/sets/*`: sends an old link to the Recipes page it meant. */
export function LegacySetsRedirect() {
  const { pathname, search, hash } = useLocation();
  return createElement(Navigate, {
    to: { pathname: recipesPathFor(pathname), search, hash },
    replace: true,
  });
}
