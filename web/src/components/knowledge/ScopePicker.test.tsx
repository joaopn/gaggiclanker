import { screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { KnowledgeInsightScope } from "@/api/types";
import { ScopePicker, scopeLabels } from "@/components/knowledge/ScopePicker";
import { renderWithQueryClient, setupUser } from "@/test/renderWithQueryClient";

const { getScopeOptions, getScopeReach } = vi.hoisted(() => ({
  getScopeOptions: vi.fn(),
  getScopeReach: vi.fn(),
}));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  getScopeOptions,
  getScopeReach,
}));

const RECIPE_OPTIONS = {
  recipe: [
    { key: "bean_id", value: 5, label: "bean Guji" },
    { key: "roast_level", value: "light", label: "roast light" },
    { key: "grinder_id", value: 2, label: "grinder Niche Zero" },
  ],
  beans: [],
  grinders: [],
  roast_levels: [],
  processes: [],
  origins: [],
};
const ARCHIVE_OPTIONS = {
  recipe: null,
  beans: [{ id: 5, label: "Guji" }],
  grinders: [
    { id: 2, label: "Niche Zero" },
    { id: 3, label: "Comandante" },
  ],
  roast_levels: [{ value: "light", label: "light" }],
  processes: [{ value: "washed", label: "washed" }],
  origins: [{ value: "Ethiopia", label: "Ethiopia" }],
};

beforeEach(() => {
  vi.clearAllMocks();
  getScopeOptions.mockResolvedValue(RECIPE_OPTIONS);
  // The reach depends on the scope asked about, as the server's does.
  getScopeReach.mockImplementation(async (scope: KnowledgeInsightScope) => ({
    recipes: Object.keys(scope).length === 0 ? 7 : 1,
  }));
});

function Harness({
  recipeId,
  initial = {},
  onScope,
  named,
}: {
  recipeId: number | null;
  initial?: KnowledgeInsightScope;
  onScope?: (scope: KnowledgeInsightScope) => void;
  named?: string[];
}) {
  const [scope, setScope] = useState<KnowledgeInsightScope>(initial);
  return (
    <ScopePicker
      recipeId={recipeId}
      named={named}
      value={scope}
      onChange={(next) => {
        setScope(next);
        onScope?.(next);
      }}
    />
  );
}

describe("ScopePicker with a Recipe", () => {
  it("is a checkbox per thing the Recipe states, by name, with the agent's choice ticked", async () => {
    renderWithQueryClient(<Harness recipeId={3} initial={{ grinder_id: 2 }} />);

    expect(await screen.findByRole("checkbox", { name: "grinder Niche Zero" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "bean Guji" })).not.toBeChecked();
    expect(screen.getByRole("checkbox", { name: "roast light" })).not.toBeChecked();
    expect(getScopeOptions).toHaveBeenCalledWith(3);
    expect(screen.getByTestId("scope-picker")).not.toHaveTextContent(/_id/);
  });

  it("says what nothing ticked means and how many Recipes the scope matches, again on every change", async () => {
    const user = setupUser();
    const seen = vi.fn();
    renderWithQueryClient(<Harness recipeId={3} onScope={seen} />);

    const summary = await screen.findByTestId("scope-picker-summary");
    await waitFor(() =>
      expect(summary).toHaveTextContent("Nothing ticked: every Recipe. Matches 7 Recipes now."),
    );

    await user.click(screen.getByRole("checkbox", { name: "bean Guji" }));
    expect(seen).toHaveBeenLastCalledWith({ bean_id: 5 });
    await waitFor(() => expect(summary).toHaveTextContent("Matches 1 Recipe now."));
    expect(summary).not.toHaveTextContent("Nothing ticked");
    expect(getScopeReach).toHaveBeenLastCalledWith({ bean_id: 5 });

    await user.click(screen.getByRole("checkbox", { name: "bean Guji" }));
    expect(seen).toHaveBeenLastCalledWith({});
    await waitFor(() => expect(summary).toHaveTextContent("Nothing ticked: every Recipe."));
  });
});

describe("ScopePicker with a value the Recipe does not offer", () => {
  it("keeps it as a ticked box with its name, so what is shown is what is saved", async () => {
    const user = setupUser();
    const seen = vi.fn();
    getScopeOptions.mockResolvedValue({
      ...RECIPE_OPTIONS,
      beans: [{ id: 7, label: "Old bag" }],
    });
    renderWithQueryClient(
      <Harness recipeId={3} initial={{ bean_id: 7, grinder_id: 2 }} onScope={seen} />,
    );

    // The Recipe now states bean 5; the scope still names bean 7.
    const held = await screen.findByRole("checkbox", {
      name: "bean Old bag (not offered here any more)",
    });
    expect(held).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "bean Guji" })).not.toBeChecked();
    expect(screen.getByRole("checkbox", { name: "grinder Niche Zero" })).toBeChecked();
    await waitFor(() =>
      expect(getScopeReach).toHaveBeenLastCalledWith({ bean_id: 7, grinder_id: 2 }),
    );

    // Unticking drops exactly that value.
    await user.click(held);
    expect(seen).toHaveBeenLastCalledWith({ grinder_id: 2 });
    expect(
      screen.queryByRole("checkbox", { name: /not offered here any more/ }),
    ).not.toBeInTheDocument();
  });

  it("says a bean that is gone from the archive in words, never an id", async () => {
    renderWithQueryClient(<Harness recipeId={3} initial={{ bean_id: 99 }} />);

    expect(
      await screen.findByRole("checkbox", {
        name: "a bean not offered here any more",
      }),
    ).toBeChecked();
  });
});

describe("ScopePicker without a Recipe", () => {
  beforeEach(() => getScopeOptions.mockResolvedValue(ARCHIVE_OPTIONS));

  it("is a select per key with 'any', and the summary says chosen rather than ticked", async () => {
    const user = setupUser();
    const seen = vi.fn();
    renderWithQueryClient(<Harness recipeId={null} onScope={seen} />);

    const grinder = await screen.findByLabelText("Grinder");
    for (const label of ["Bean", "Roast", "Process", "Origin"]) {
      expect(screen.getByLabelText(label)).toHaveValue("");
    }
    expect(await screen.findByRole("option", { name: "Comandante" })).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByTestId("scope-picker-summary")).toHaveTextContent(
        "Nothing chosen: every Recipe.",
      ),
    );

    await user.selectOptions(grinder, "Niche Zero");
    expect(seen).toHaveBeenLastCalledWith({ grinder_id: 2 });
    await user.selectOptions(screen.getByLabelText("Roast"), "light");
    expect(seen).toHaveBeenLastCalledWith({ grinder_id: 2, roast_level: "light" });
    await user.selectOptions(grinder, "");
    expect(seen).toHaveBeenLastCalledWith({ roast_level: "light" });
    expect(getScopeOptions).toHaveBeenCalledWith(null);
  });
});

describe("ScopePicker selects holding a value no option offers", () => {
  beforeEach(() => getScopeOptions.mockResolvedValue(ARCHIVE_OPTIONS));

  it("shows it as its own selected option, counts it as chosen, and drops it when another is picked", async () => {
    const user = setupUser();
    const seen = vi.fn();
    // Bean 2 was archived after the request: the select no longer lists it.
    renderWithQueryClient(
      <Harness recipeId={null} initial={{ bean_id: 2, roast_level: "dark" }} onScope={seen} />,
    );

    const bean = await screen.findByLabelText("Bean");
    await screen.findByRole("option", {
      name: "a bean not offered here any more",
    });
    expect(bean).toHaveValue("2");
    // The roast too: "dark" is not among the offered levels.
    expect(screen.getByLabelText("Roast")).toHaveValue("dark");
    expect(
      screen.getByRole("option", { name: "roast dark (not offered here any more)" }),
    ).toBeInTheDocument();
    expect(screen.getByTestId("scope-picker-summary")).not.toHaveTextContent("Nothing chosen");
    expect(screen.getByLabelText("Grinder")).toHaveValue("");

    await user.selectOptions(bean, "Guji");
    expect(seen).toHaveBeenLastCalledWith({ bean_id: 5, roast_level: "dark" });
    expect(
      screen.queryByRole("option", { name: /a bean not offered here any more/ }),
    ).not.toBeInTheDocument();
  });

  it("names a stale bean or grinder by what the request said, when it said", async () => {
    getScopeOptions.mockResolvedValue({ ...ARCHIVE_OPTIONS, beans: [], grinders: [] });
    renderWithQueryClient(
      <Harness
        recipeId={null}
        initial={{ bean_id: 2, grinder_id: 9 }}
        named={["grinder Mazzer", "bean Ethiopia Guji"]}
      />,
    );

    expect(
      await screen.findByRole("option", { name: "bean Ethiopia Guji (not offered here any more)" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("option", { name: "grinder Mazzer (not offered here any more)" }),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Bean")).toHaveValue("2");
    expect(screen.getByLabelText("Grinder")).toHaveValue("9");
  });

  it("takes the offered option's own spelling when the stored value differs only in case or spaces", async () => {
    const seen = vi.fn();
    getScopeOptions.mockResolvedValue({
      ...ARCHIVE_OPTIONS,
      origins: [{ value: "RWANDA", label: "RWANDA" }],
    });
    renderWithQueryClient(
      <Harness recipeId={null} initial={{ origin: " Rwanda " }} onScope={seen} />,
    );

    await screen.findByRole("option", { name: "RWANDA" });
    // It shows that option, not "any", and invents no stale one.
    expect(screen.getByLabelText("Origin")).toHaveValue("RWANDA");
    expect(
      screen.queryByRole("option", { name: /not offered here any more/ }),
    ).not.toBeInTheDocument();
    expect(screen.getByTestId("scope-picker-summary")).not.toHaveTextContent("Nothing chosen");
  });

  it("does not invent one for a value the select does offer", async () => {
    renderWithQueryClient(
      <Harness recipeId={null} initial={{ grinder_id: 2, origin: "ethiopia" }} />,
    );

    await screen.findByRole("option", { name: "Comandante" });
    expect(
      screen.queryByRole("option", { name: /not offered here any more/ }),
    ).not.toBeInTheDocument();
  });
});

describe("scopeLabels", () => {
  it("names a Recipe's own attributes, the archive's beans and grinders, and shows no id", () => {
    expect(scopeLabels({ grinder_id: 2, bean_id: 5 }, RECIPE_OPTIONS)).toEqual([
      "bean Guji",
      "grinder Niche Zero",
    ]);
    expect(scopeLabels({ grinder_id: 3, roast_level: "dark" }, ARCHIVE_OPTIONS)).toEqual([
      "roast dark",
      "grinder Comandante",
    ]);
    expect(scopeLabels({ grinder_id: 99 }, ARCHIVE_OPTIONS)).toEqual([
      "a grinder not offered here any more",
    ]);
    expect(scopeLabels({}, ARCHIVE_OPTIONS)).toEqual([]);
  });
});
