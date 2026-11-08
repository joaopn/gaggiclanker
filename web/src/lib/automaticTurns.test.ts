import { describe, expect, it } from "vitest";
import {
  acceptedMessage,
  approvedProfileMessage,
  declinedMessage,
} from "@/components/chat/tellAgent";
import { isAutomaticTurn } from "@/lib/automaticTurns";
import turns from "@/lib/profileTurns.json";
import { designProposal, proposal } from "@/test/recipesFixtures";

describe("isAutomaticTurn", () => {
  it("is true for every turn a card builds, and false for the person's own words", () => {
    const built = [
      declinedMessage(""),
      declinedMessage("too cold"),
      approvedProfileMessage({ name: "A", newVersion: true }),
      approvedProfileMessage({ name: "A", newVersion: false, proposedAs: "B" }),
      approvedProfileMessage({
        name: "A",
        newVersion: false,
        forRecipe: { versionLabel: "v2", recipeName: "Guji" },
      }),
    ];
    for (const turn of built) expect(isAutomaticTurn(turn), turn).toBe(true);
    const decision = { version: { version_label: "v2" } };
    expect(
      isAutomaticTurn(acceptedMessage({ ...decision, proposal: proposal() } as never) ?? ""),
    ).toBe(true);
    expect(
      isAutomaticTurn(acceptedMessage({ ...decision, proposal: designProposal() } as never) ?? ""),
    ).toBe(true);
    for (const template of Object.entries(turns)
      .filter(([key]) => key !== "automatic_turn_prefixes")
      .map(([, value]) => value)
      .filter((value): value is string => typeof value === "string" && /^[A-Z]/.test(value))) {
      expect(isAutomaticTurn(template), template).toBe(true);
    }
    expect(isAutomaticTurn("never suggest going finer than 8 on the Niche")).toBe(false);
    expect(isAutomaticTurn("I Accepted: it earlier")).toBe(false);
  });

  it("looks past leading whitespace, which a stored turn may carry", () => {
    for (const turn of ["  Declined: no reason given.", "\n\tApproved: Turbo, a new profile."]) {
      expect(isAutomaticTurn(turn), turn).toBe(true);
    }
    expect(isAutomaticTurn("   never suggest going finer")).toBe(false);
  });
});
