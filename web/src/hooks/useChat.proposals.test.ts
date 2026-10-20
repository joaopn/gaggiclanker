import { describe, expect, it } from "vitest";
import { proposalFrom } from "@/components/chat/ProposeCard";
import { draftedId, proposedSetId } from "@/hooks/useChat";
import { currentToolName, LEGACY_TOOL_NAMES } from "@/lib/legacyTools";

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
      recipe_id: 3,
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
    expect(proposedSetId(JSON.stringify({ proposal_id: 5, recipe_id: "three" }))).toBeNull();
  });
});

describe("draftedId", () => {
  it("finds the draft a drafted profile version or a first recipe made", () => {
    expect(draftedId(JSON.stringify({ draft_id: 12, status: "draft" }))).toBe(12);
    expect(draftedId(JSON.stringify({ proposal_id: 5, recipe_id: 3, draft_id: 14 }))).toBe(14);
  });

  it("ignores a result with no draft, and anything it cannot read", () => {
    expect(draftedId(JSON.stringify({ proposal_id: 5, recipe_id: 3 }))).toBeNull();
    expect(draftedId(JSON.stringify({ draft_id: "twelve" }))).toBeNull();
    expect(draftedId(JSON.stringify({ draft_id: 0 }))).toBeNull();
    expect(draftedId(undefined)).toBeNull();
    expect(draftedId("not json")).toBeNull();
    expect(draftedId("[1]")).toBeNull();
  });
});

/**
 * A conversation made before the rename keeps the names it was made under: its stored tool calls
 * are `propose_set_version` and its results name the Recipe `set_id`. Its cards must still show.
 */
describe("a conversation stored under the old names", () => {
  const oldChange = {
    id: "c1",
    name: "propose_set_version",
    arguments: {},
    ok: true,
    content: JSON.stringify({
      proposal_id: 5,
      set_id: 3,
      status: "proposed",
      changed: ["the dose"],
      change_summary: "dose 18 g to 18.5 g",
    }),
  };

  it("maps each old tool name to the one it has now", () => {
    expect(LEGACY_TOOL_NAMES).toEqual({
      get_set: "get_recipe",
      list_sets: "list_recipes",
      list_set_shots: "list_recipe_shots",
      propose_set_version: "propose_recipe_version",
    });
    expect(currentToolName("propose_set_version")).toBe("propose_recipe_version");
    expect(currentToolName("propose_outcome")).toBe("propose_outcome");
  });

  it("still shows the change card for an old propose_set_version call", () => {
    const card = proposalFrom(oldChange);
    expect(card?.kind).toBe("recipe_version");
    expect(card?.setId).toBe(3);
    expect(card?.proposalId).toBe(5);
  });

  it("shows the same card for a call made under the new name, with the new key", () => {
    const fresh = {
      ...oldChange,
      name: "propose_recipe_version",
      content: JSON.stringify({ proposal_id: 5, recipe_id: 3, changed: [] }),
    };
    expect(proposalFrom(fresh)?.setId).toBe(3);
  });

  it("refreshes from an old result, which names the Recipe under set_id", () => {
    expect(proposedSetId(oldChange.content)).toBe(3);
  });

  it("reads an old initial recipe, outcome and deletion result the same way", () => {
    for (const name of ["propose_initial_recipe", "propose_outcome", "propose_insight_deletion"]) {
      const content = JSON.stringify({
        proposal_id: 8,
        set_id: 4,
        version: "v1",
        outcome: "held",
        counted_shots: 2,
        insight_text: "a lesson",
        recipe: {},
      });
      expect(proposalFrom({ id: "c2", name, arguments: {}, ok: true, content })?.setId).toBe(4);
    }
  });
});
