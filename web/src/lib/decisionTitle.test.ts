import { describe, expect, it } from "vitest";
import { derivedDecisionTitle } from "@/lib/decisionTitle";

describe("derivedDecisionTitle", () => {
  it("names the rating the server picked and where it sits, and the rule for the word", () => {
    expect(
      derivedDecisionTitle({
        derived_decision: "keep",
        decision_rating: 4,
        decision_rating_source: "machine_notes",
      }),
    ).toBe(
      "From the 4★ rating on the machine's notes card (3★ or more is Good). Click to confirm or change.",
    );
    expect(
      derivedDecisionTitle({
        derived_decision: "improve",
        decision_rating: 2,
        decision_rating_source: "judgement",
      }),
    ).toBe("From your 2★ rating (below 3★ is Bad). Click to confirm or change.");
    expect(
      derivedDecisionTitle({
        derived_decision: "keep",
        decision_rating: 5,
        decision_rating_source: "machine_index",
      }),
    ).toBe(
      "From the 5★ rating on the machine's index (3★ or more is Good). Click to confirm or change.",
    );
  });

  it("says no more than the rule when the server served no rating", () => {
    expect(derivedDecisionTitle({ derived_decision: "keep" })).toBe(
      "From the rating (3★ or more is Good). Click to confirm or change.",
    );
  });
});
