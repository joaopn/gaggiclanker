import { describe, expect, it } from "vitest";
import type { RecipeProposalDecision } from "@/api/types";
import {
  acceptedMessage,
  approvedProfileMessage,
  declinedMessage,
} from "@/components/chat/tellAgent";

const design = {
  proposal: { kind: "design", changes: [] },
  version: { version_label: "v1" },
} as unknown as RecipeProposalDecision;

describe("the turns a profile card sends", () => {
  it("names the version of the Recipe for a profile proposed for it", () => {
    expect(
      approvedProfileMessage({
        name: "Adaptive Bloom",
        forRecipe: { versionLabel: "v1.2", recipeName: "Guji natural" },
        newVersion: true,
      }),
    ).toBe("Approved: Adaptive Bloom as v1.2 of Guji natural.");
  });

  it("says a new version of a profile, or a new profile, outside a Recipe", () => {
    expect(approvedProfileMessage({ name: "Adaptive Bloom", newVersion: true })).toBe(
      "Approved: Adaptive Bloom, a new version of that profile.",
    );
    expect(approvedProfileMessage({ name: "Gentle Bloom", newVersion: false })).toBe(
      "Approved: Gentle Bloom, a new profile.",
    );
  });

  it("ends a renamed profile's turn with what the agent proposed, in place of the full stop", () => {
    expect(
      approvedProfileMessage({ name: "Gentle Bloom", newVersion: false, proposedAs: "Soft Bloom" }),
    ).toBe("Approved: Gentle Bloom, a new profile (you proposed it as Soft Bloom).");
    expect(
      approvedProfileMessage({
        name: "Gentle Bloom",
        forRecipe: { versionLabel: "v1.2", recipeName: "Guji" },
        newVersion: false,
        proposedAs: "Soft Bloom",
      }),
    ).toBe("Approved: Gentle Bloom as v1.2 of Guji (you proposed it as Soft Bloom).");
  });

  it("fills a typed name once, whatever it contains", () => {
    expect(
      approvedProfileMessage({ name: "<old name> $& <name>", newVersion: false, proposedAs: "X" }),
    ).toBe("Approved: <old name> $& <name>, a new profile (you proposed it as X).");
    expect(declinedMessage("too <note> $1 sweet")).toBe("Declined: too <note> $1 sweet");
  });

  it("declines with the note, or says there was none", () => {
    expect(declinedMessage("Too aggressive for a light roast.")).toBe(
      "Declined: Too aggressive for a light roast.",
    );
    expect(declinedMessage("")).toBe("Declined: no reason given.");
  });

  it("tells a first recipe's accept, and the new name of its profile when it was renamed", () => {
    expect(acceptedMessage(design)).toBe("Accepted: your first version is now v1 of this Recipe.");
    expect(acceptedMessage(design, { name: "Gentle Bloom", proposedAs: "Soft Bloom" })).toBe(
      "Accepted: your first version is now v1 of this Recipe, with its profile named Gentle Bloom (you proposed it as Soft Bloom).",
    );
  });
});
