import { describe, expect, it } from "vitest";
import { proposedSetId } from "@/hooks/useChat";

/**
 * Which tool results make a proposal card's buttons appear mid-answer.
 *
 * The card reads the proposal row back to know whether it is still a question,
 * and a run can write three more paragraphs after the tool returns. Waiting for
 * `completed` would leave the person looking at a card with no buttons for as
 * long as the model keeps talking — so the result itself is what refreshes it.
 *
 * The narrowness is the point as much as the refresh: every other tool result
 * in the stream must invalidate nothing.
 */
describe("proposedSetId", () => {
  it("finds the Set a proposed change belongs to", () => {
    const content = JSON.stringify({
      proposal_id: 5,
      set_id: 3,
      status: "proposed",
      changed: ["the dose"],
    });
    expect(proposedSetId(content)).toBe(3);
  });

  it("ignores a tool result that proposed nothing", () => {
    expect(proposedSetId(JSON.stringify({ shot: { shot_id: 129 } }))).toBeNull();
    expect(proposedSetId(JSON.stringify({ draft_id: 12 }))).toBeNull();
    expect(proposedSetId(JSON.stringify({ insight_id: 7 }))).toBeNull();
  });

  it("ignores anything it cannot read, rather than guessing", () => {
    expect(proposedSetId(undefined)).toBeNull();
    expect(proposedSetId("")).toBeNull();
    expect(proposedSetId("not json")).toBeNull();
    expect(proposedSetId("[1, 2, 3]")).toBeNull();
    expect(proposedSetId(JSON.stringify({ proposal_id: 5 }))).toBeNull();
    expect(proposedSetId(JSON.stringify({ proposal_id: 5, set_id: "three" }))).toBeNull();
  });
});
