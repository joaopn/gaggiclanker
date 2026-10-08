import { screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { SignatureOverride } from "@/api/types";
import { VersionOverrides } from "@/components/sets/VersionOverrides";
import { renderWithQueryClient, setupUser } from "@/test/renderWithQueryClient";
import {
  overrideInForce,
  overrideRejected,
  SIGNATURE_PROFILE_VERSION_ID,
  SIGNATURE_SET_ID,
  SIGNATURE_SET_VERSION_ID,
} from "@/test/signatureFixtures";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
  Toaster: () => null,
}));

const api = vi.hoisted(() => ({
  getSignatureOverrides: vi.fn(),
  rejectSignatureOverride: vi.fn(),
  restoreSignatureOverride: vi.fn(),
}));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  ...api,
}));

function renderOverrides(items: SignatureOverride[], profileVersionId: number | null = 1) {
  api.getSignatureOverrides.mockResolvedValue({ items });
  return renderWithQueryClient(
    <VersionOverrides
      setId={SIGNATURE_SET_ID}
      versionId={SIGNATURE_SET_VERSION_ID}
      profileVersionId={profileVersionId}
    />,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  api.rejectSignatureOverride.mockResolvedValue({ override: overrideRejected });
  api.restoreSignatureOverride.mockResolvedValue({ override: overrideInForce });
});

describe("VersionOverrides", () => {
  it("says an override in force beside the profile's own limit, and offers Reject", async () => {
    renderOverrides([overrideInForce]);

    const item = await screen.findByTestId("version-override");
    expect(item).toHaveAttribute("data-status", "confirmed");
    expect(within(item).getByTestId("override-limit")).toHaveTextContent(
      "at most 20 % of target here (profile: at most 15 % of target)",
    );
    expect(within(item).getByTestId("override-status")).toHaveTextContent("In force");
    expect(within(item).getByTestId("override-reject")).toBeInTheDocument();
    expect(within(item).queryByTestId("override-restore")).toBeNull();
    // Nothing is waiting for a confirmation, and nothing offers one.
    expect(within(item).queryByTestId("override-confirm")).toBeNull();
    expect(within(item).queryByTestId("override-withdraw")).toBeNull();
  });

  it("draws nothing for a version with no override, or no profile", async () => {
    const { container } = renderOverrides([]);
    await waitFor(() => expect(api.getSignatureOverrides).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();

    api.getSignatureOverrides.mockClear();
    const none = renderOverrides([overrideInForce], null);
    expect(none.container).toBeEmptyDOMElement();
    expect(api.getSignatureOverrides).not.toHaveBeenCalled();
  });

  it("rejects with the reason typed, once", async () => {
    const user = setupUser();
    api.rejectSignatureOverride.mockReturnValue(new Promise(() => undefined));
    renderOverrides([overrideInForce]);

    await user.click(await screen.findByTestId("override-reject"));
    await user.type(screen.getByLabelText(/why reject the ramp override/i), "not this bean");
    await user.dblClick(screen.getByTestId("reason-submit"));

    expect(api.rejectSignatureOverride).toHaveBeenCalledTimes(1);
    expect(api.rejectSignatureOverride).toHaveBeenCalledWith(
      SIGNATURE_SET_ID,
      overrideInForce.id,
      "not this bean",
    );
  });

  it("restores a rejected override with one call under a double press", async () => {
    const user = setupUser();
    api.restoreSignatureOverride.mockReturnValue(new Promise(() => undefined));
    renderOverrides([overrideRejected]);

    await user.dblClick(await screen.findByTestId("override-restore"));

    expect(api.restoreSignatureOverride).toHaveBeenCalledTimes(1);
    expect(api.restoreSignatureOverride).toHaveBeenCalledWith(
      SIGNATURE_SET_ID,
      overrideRejected.id,
    );
  });

  it("cannot restore an override of an expectation that is out of force, and links to where that is done", async () => {
    const user = setupUser();
    renderOverrides([{ ...overrideRejected, expectation_status: "rejected" }]);

    const restore = await screen.findByTestId("override-restore");
    expect(restore).toHaveAttribute("aria-disabled", "true");
    await user.click(restore);
    expect(api.restoreSignatureOverride).not.toHaveBeenCalled();
    expect(screen.getByTestId("override-profile-link")).toHaveAttribute(
      "href",
      `/profiles#version-${SIGNATURE_PROFILE_VERSION_ID}`,
    );
  });

  it("shows a rejected override with its reason and Restore, and a replaced one as a record with nothing to press", async () => {
    renderOverrides([
      { ...overrideRejected, id: 91, reject_reason: "not this bean" },
      { ...overrideInForce, id: 92, status: "withdrawn" },
    ]);

    const items = await screen.findAllByTestId("version-override");
    expect(items.map((i) => i.getAttribute("data-status"))).toEqual(["rejected", "withdrawn"]);
    expect(items[0]).toHaveTextContent("Rejected: not this bean");
    expect(within(items[0]).getByTestId("override-restore")).toBeInTheDocument();
    expect(within(items[1]).getByTestId("override-status")).toHaveTextContent("No longer in force");
    // Not "here": a limit nobody reads is only what was proposed.
    for (const item of items) {
      expect(within(item).getByTestId("override-limit")).toHaveTextContent(
        "Proposed at most 20 % of target (profile: at most 15 % of target)",
      );
      expect(within(item).getByTestId("override-limit")).not.toHaveTextContent("here");
    }
    expect(within(items[1]).queryAllByRole("button")).toHaveLength(0);
  });

  it("shows an override proposed before they were in force at once as not in force, with Reject", async () => {
    renderOverrides([{ ...overrideInForce, status: "proposed" }]);

    const item = await screen.findByTestId("version-override");
    expect(within(item).getByTestId("override-status")).toHaveTextContent("Not in force");
    expect(within(item).getByTestId("override-limit")).not.toHaveTextContent("here");
    expect(within(item).getByTestId("override-reject")).toBeInTheDocument();
    expect(within(item).queryByTestId("override-restore")).toBeNull();
  });

  it("opens the reason form from a Reject that is never disabled", async () => {
    renderOverrides([overrideInForce]);

    const reject = await screen.findByTestId("override-reject");
    expect(reject).not.toBeDisabled();
    expect(reject).toHaveAttribute("aria-disabled", "false");
  });

  it("links the conversation that proposed it", async () => {
    renderOverrides([overrideInForce]);

    expect(await screen.findByTestId("override-thread-link")).toHaveAttribute(
      "href",
      `/chat?thread=${overrideInForce.proposed_by_thread_id}`,
    );
  });
});

describe("focus", () => {
  it("moves to the override when Reject takes its own button away, on Cancel and on submit", async () => {
    const user = setupUser();
    renderOverrides([overrideInForce]);
    const item = await screen.findByTestId("version-override");

    await user.click(within(item).getByTestId("override-reject"));
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(item).toHaveFocus();
    await user.click(within(item).getByTestId("override-reject"));
    await user.click(screen.getByTestId("reason-submit"));
    expect(item).toHaveFocus();
  });

  it("keeps focus on the override through a double click on Restore", async () => {
    const user = setupUser();
    api.restoreSignatureOverride.mockReturnValue(new Promise(() => undefined));
    renderOverrides([overrideRejected]);
    const item = await screen.findByTestId("version-override");

    await user.dblClick(within(item).getByTestId("override-restore"));

    expect(api.restoreSignatureOverride).toHaveBeenCalledTimes(1);
    expect(item).toHaveFocus();
  });

  it("marks Restore aria-disabled while the call is out, and never disables it", async () => {
    const user = setupUser();
    api.restoreSignatureOverride.mockReturnValue(new Promise(() => undefined));
    renderOverrides([overrideRejected]);
    const button = await screen.findByTestId("override-restore");
    expect(button).toHaveAttribute("aria-disabled", "false");

    await user.click(button);

    await waitFor(() => expect(button).toHaveAttribute("aria-disabled", "true"));
    expect(button).not.toBeDisabled();
  });
});
