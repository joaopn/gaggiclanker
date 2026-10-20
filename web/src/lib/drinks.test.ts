import { describe, expect, it } from "vitest";
import type { FieldChange } from "@/api/types";
import { drinkChangeLine, drinkLabel, notesVerb } from "@/lib/drinks";
import { vocabulary } from "@/test/recipesFixtures";

function change(field: string, before: string | null, after: string | null): FieldChange {
  return { field, label: field, before, after, from_profile: false };
}

describe("drinkLabel", () => {
  it("is the vocabulary's word, and nothing for no drink", () => {
    expect(drinkLabel(vocabulary.drinks, "latte")).toBe("Latte");
    expect(drinkLabel(vocabulary.drinks, null)).toBe("");
  });

  it("falls back to the value, capitalised, while the vocabulary is in flight", () => {
    expect(drinkLabel(undefined, "americano")).toBe("Americano");
  });
});

describe("notesVerb", () => {
  it("reads added, changed and removed off the two sides", () => {
    expect(notesVerb({ before: null, after: "oat milk" })).toBe("added");
    expect(notesVerb({ before: "oat milk", after: "whole milk" })).toBe("changed");
    expect(notesVerb({ before: "oat milk", after: null })).toBe("removed");
  });
});

describe("drinkChangeLine", () => {
  it("names the drink change and the notes together", () => {
    expect(
      drinkChangeLine([
        change("drink", "Espresso", "Cappuccino"),
        change("drink_notes", null, "oat milk"),
      ]),
    ).toBe("Drink: Espresso → Cappuccino · notes added");
  });

  it("names only the new drink when the parent stated none", () => {
    expect(drinkChangeLine([change("drink", null, "Cappuccino")])).toBe("Drink: Cappuccino");
  });

  it("says the notes alone", () => {
    expect(drinkChangeLine([change("drink_notes", "a", "b")])).toBe("notes changed");
    expect(drinkChangeLine([change("drink_notes", "a", null)])).toBe("notes removed");
  });

  it("is null when neither moved", () => {
    expect(drinkChangeLine([change("grind_setting", "22", "21")])).toBeNull();
  });
});
