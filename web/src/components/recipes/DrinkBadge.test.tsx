import { readFileSync } from "node:fs";
import path from "node:path";
import { screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { DrinkBadge } from "@/components/recipes/DrinkBadge";
import { vocabulary } from "@/test/recipesFixtures";
import { renderWithQueryClient } from "@/test/renderWithQueryClient";

const { getVocabulary } = vi.hoisted(() => ({ getVocabulary: vi.fn() }));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  getVocabulary,
}));

beforeEach(() => {
  vi.clearAllMocks();
  getVocabulary.mockResolvedValue(vocabulary);
});

describe("DrinkBadge", () => {
  it("prints the vocabulary's word for the drink", async () => {
    renderWithQueryClient(<DrinkBadge drink="cappuccino" />);
    const badge = await screen.findByText("Cappuccino");
    expect(badge).toHaveAttribute("data-testid", "drink-badge");
    expect(badge).toHaveAttribute("data-drink", "cappuccino");
  });

  it("prints the vocabulary's word, not the value dressed up", async () => {
    // A label that is not the value capitalised: a badge that built its word from the value
    // would print "Cappuccino" here.
    getVocabulary.mockResolvedValue({
      ...vocabulary,
      drinks: vocabulary.drinks.map((drink) =>
        drink.value === "cappuccino" ? { ...drink, label: "Cappuccino (milk)" } : drink,
      ),
    });
    renderWithQueryClient(<DrinkBadge drink="cappuccino" />);
    expect(await screen.findByText("Cappuccino (milk)")).toHaveAttribute(
      "data-drink",
      "cappuccino",
    );
  });

  it("renders nothing for no drink, and asks for no vocabulary", () => {
    const { container } = renderWithQueryClient(<DrinkBadge drink={null} />);
    expect(container).toBeEmptyDOMElement();
    expect(getVocabulary).not.toHaveBeenCalled();
  });

  it("reads its colours from one token set per drink, with nothing shrinkable on the word", async () => {
    renderWithQueryClient(<DrinkBadge drink="latte" />);
    const badge = await screen.findByText("Latte");
    expect(badge.style.backgroundColor).toContain("--drink-latte-bg");
    expect(badge.style.color).toContain("--drink-latte-fg");
    expect(badge.style.borderColor).toContain("--drink-latte-border");
    // The word is never cut: no shrink factor, no truncation, no wrapping.
    expect(badge.className).toContain("shrink-0");
    expect(badge.className).toContain("whitespace-nowrap");
    expect(badge.className).not.toMatch(/(^|\s)(truncate|shrink)(\s|$)|shrink-\[/);
    expect(badge.children).toHaveLength(0);
  });

  it("falls back to Other's colours for a drink the stylesheet has no set for", async () => {
    renderWithQueryClient(<DrinkBadge drink="flat_white" />);
    const badge = await screen.findByText("Flat_white");
    expect(badge.style.backgroundColor).toContain("var(--drink-other-bg)");
  });
});

describe("the drink tokens in index.css", () => {
  const css = readFileSync(path.resolve(__dirname, "../../index.css"), "utf8");
  const light = css.slice(css.indexOf(":root {"), css.indexOf(':root[data-theme="roast"]'));
  const dark = css.slice(css.indexOf(':root[data-theme="roast"]'), css.indexOf("@layer base"));

  function token(block: string, name: string): string {
    const found = block.match(new RegExp(`--${name}:\\s*(#[0-9a-fA-F]{6})\\s*;`));
    if (!found) throw new Error(`--${name} is not a hex colour in the block`);
    return found[1];
  }

  function luminance(hex: string): number {
    const channels = [1, 3, 5].map((i) => {
      const value = Number.parseInt(hex.slice(i, i + 2), 16) / 255;
      return value <= 0.03928 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
    });
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2];
  }

  function contrast(a: string, b: string): number {
    const [high, low] = [luminance(a), luminance(b)].sort((x, y) => y - x);
    return (high + 0.05) / (low + 0.05);
  }

  // The drinks come from the served vocabulary, so a sixth drink without tokens fails here.
  const drinks = vocabulary.drinks.map((drink) => drink.value);

  for (const [theme, block] of [
    ["light", light],
    ["dark", dark],
  ] as const) {
    for (const drink of drinks) {
      it(`${drink} text is AA on its background in the ${theme} theme`, () => {
        const ratio = contrast(
          token(block, `drink-${drink}-fg`),
          token(block, `drink-${drink}-bg`),
        );
        expect(ratio).toBeGreaterThanOrEqual(4.5);
      });
    }
  }

  it("gives Latte an outline that reads against the card in both themes", () => {
    expect(
      contrast(token(light, "drink-latte-border"), token(light, "card")),
    ).toBeGreaterThanOrEqual(3);
    expect(contrast(token(dark, "drink-latte-border"), token(dark, "card"))).toBeGreaterThanOrEqual(
      3,
    );
  });

  it("keeps the Latte outline readable against the page background, where the heading badge sits", () => {
    expect(
      contrast(token(light, "drink-latte-border"), token(light, "background")),
    ).toBeGreaterThanOrEqual(3);
    expect(
      contrast(token(dark, "drink-latte-border"), token(dark, "background")),
    ).toBeGreaterThanOrEqual(3);
  });

  it("keeps the five drinks apart: no two share a background in a theme", () => {
    for (const block of [light, dark]) {
      const backgrounds = drinks.map((drink) => token(block, `drink-${drink}-bg`));
      expect(new Set(backgrounds).size).toBe(drinks.length);
    }
  });
});
