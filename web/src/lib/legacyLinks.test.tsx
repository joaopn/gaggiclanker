import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { LegacySetsRedirect, recipesPathFor, withRecipeParam } from "@/lib/legacyLinks";

describe("withRecipeParam", () => {
  it("moves an old set= to recipe=, keeping every other parameter", () => {
    const moved = withRecipeParam(new URLSearchParams("set=3&version=22&ask=Why%3F"));
    expect(moved?.get("recipe")).toBe("3");
    expect(moved?.has("set")).toBe(false);
    expect(moved?.get("version")).toBe("22");
    expect(moved?.get("ask")).toBe("Why?");
  });

  it("moves the shots page's set=needs too", () => {
    expect(withRecipeParam(new URLSearchParams("set=needs"))?.toString()).toBe("recipe=needs");
  });

  it("has nothing to move without set=, and lets recipe= win when both are there", () => {
    expect(withRecipeParam(new URLSearchParams("recipe=3&version=22"))).toBeNull();
    expect(withRecipeParam(new URLSearchParams(""))).toBeNull();
    expect(withRecipeParam(new URLSearchParams("set=3&recipe=5"))?.toString()).toBe("recipe=5");
  });
});

describe("recipesPathFor", () => {
  it("turns /sets and /sets/<id>... into /recipes...", () => {
    expect(recipesPathFor("/sets")).toBe("/recipes");
    expect(recipesPathFor("/sets/3")).toBe("/recipes/3");
    expect(recipesPathFor("/sets/3/anything/else")).toBe("/recipes/3/anything/else");
  });

  it("leaves every other path alone", () => {
    expect(recipesPathFor("/settings")).toBe("/settings");
    expect(recipesPathFor("/shots/3")).toBe("/shots/3");
    expect(recipesPathFor("/recipes/3")).toBe("/recipes/3");
    expect(recipesPathFor("/sets-and-more")).toBe("/sets-and-more");
  });
});

function Where() {
  const { pathname, search, hash } = useLocation();
  return <p data-testid="where">{`${pathname}${search}${hash}`}</p>;
}

function renderAt(entry: string) {
  render(
    <MemoryRouter initialEntries={[entry]}>
      <Routes>
        <Route path="/sets" element={<LegacySetsRedirect />} />
        <Route path="/sets/*" element={<LegacySetsRedirect />} />
        <Route path="*" element={<Where />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("the /sets routes", () => {
  it("send /sets to /recipes", () => {
    renderAt("/sets");
    expect(screen.getByTestId("where")).toHaveTextContent("/recipes");
  });

  it("send /sets/<id> to /recipes/<id>, keeping the query and the hash", () => {
    renderAt("/sets/9?duplicate=22&keep=1#where");
    expect(screen.getByTestId("where")).toHaveTextContent("/recipes/9?duplicate=22&keep=1#where");
  });

  it("send the tail of a deeper path along", () => {
    renderAt("/sets/9/versions/4");
    expect(screen.getByTestId("where")).toHaveTextContent("/recipes/9/versions/4");
  });
});
