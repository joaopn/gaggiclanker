import { screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { SignatureOverride } from "@/api/types";
import { VersionOverrides } from "@/components/sets/VersionOverrides";
import { renderWithQueryClient, setupUser } from "@/test/renderWithQueryClient";
import {
  overrideConfirmed,
  overrideWaiting,
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
  confirmSignatureOverride: vi.fn(),
  rejectSignatureOverride: vi.fn(),
  withdrawSignatureOverride: vi.fn(),
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
  api.confirmSignatureOverride.mockResolvedValue({ override: overrideConfirmed });
  api.rejectSignatureOverride.mockResolvedValue({ override: overrideWaiting });
  api.withdrawSignatureOverride.mockResolvedValue({ override: overrideConfirmed });
});

describe("VersionOverrides", () => {
  it("says a confirmed override beside the profile's own limit, and offers to withdraw it", async () => {
    renderOverrides([overrideConfirmed]);

    const item = await screen.findByTestId("version-override");
    expect(item).toHaveAttribute("data-status", "confirmed");
    expect(within(item).getByTestId("override-limit")).toHaveTextContent(
      "at most 20 % of target here (profile: at most 15 % of target)",
    );
    expect(within(item).getByTestId("override-status")).toHaveTextContent("Confirmed");
    expect(within(item).queryByTestId("override-confirm")).toBeNull();
    expect(within(item).getByTestId("override-withdraw")).toBeInTheDocument();
  });

  it("draws nothing for a version with no override, or no profile", async () => {
    const { container } = renderOverrides([]);
    await waitFor(() => expect(api.getSignatureOverrides).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();

    api.getSignatureOverrides.mockClear();
    const none = renderOverrides([overrideConfirmed], null);
    expect(none.container).toBeEmptyDOMElement();
    expect(api.getSignatureOverrides).not.toHaveBeenCalled();
  });

  it("confirms a proposed override with one call, however fast it is pressed twice", async () => {
    const user = setupUser();
    api.confirmSignatureOverride.mockReturnValue(new Promise(() => undefined));
    renderOverrides([overrideWaiting]);

    await user.dblClick(await screen.findByTestId("override-confirm"));

    expect(api.confirmSignatureOverride).toHaveBeenCalledTimes(1);
    expect(api.confirmSignatureOverride).toHaveBeenCalledWith(SIGNATURE_SET_ID, overrideWaiting.id);
  });

  it("rejects with the reason typed, once", async () => {
    const user = setupUser();
    api.rejectSignatureOverride.mockReturnValue(new Promise(() => undefined));
    renderOverrides([overrideWaiting]);

    await user.click(await screen.findByTestId("override-reject"));
    await user.type(screen.getByLabelText(/why reject the ramp override/i), "not this bean");
    await user.dblClick(screen.getByTestId("reason-submit"));

    expect(api.rejectSignatureOverride).toHaveBeenCalledTimes(1);
    expect(api.rejectSignatureOverride).toHaveBeenCalledWith(
      SIGNATURE_SET_ID,
      overrideWaiting.id,
      "not this bean",
    );
  });

  it("withdraws a confirmed override with one call under a double press", async () => {
    const user = setupUser();
    api.withdrawSignatureOverride.mockReturnValue(new Promise(() => undefined));
    renderOverrides([overrideConfirmed]);

    await user.dblClick(await screen.findByTestId("override-withdraw"));

    expect(api.withdrawSignatureOverride).toHaveBeenCalledTimes(1);
    expect(api.withdrawSignatureOverride).toHaveBeenCalledWith(
      SIGNATURE_SET_ID,
      overrideConfirmed.id,
    );
  });

  it("cannot confirm an override of an expectation that is not confirmed, and links to where that is done", async () => {
    renderOverrides([{ ...overrideWaiting, expectation_status: "rejected" }]);

    expect(await screen.findByTestId("override-confirm")).toBeDisabled();
    expect(screen.getByTestId("override-profile-link")).toHaveAttribute(
      "href",
      `/profiles#version-${SIGNATURE_PROFILE_VERSION_ID}`,
    );
    // Rejecting it is still open.
    expect(screen.getByTestId("override-reject")).toBeEnabled();
  });

  it("shows a rejected or withdrawn override as a record with nothing to press", async () => {
    renderOverrides([
      { ...overrideWaiting, id: 91, status: "rejected", reject_reason: "not this bean" },
      { ...overrideConfirmed, id: 92, status: "withdrawn" },
    ]);

    const items = await screen.findAllByTestId("version-override");
    expect(items.map((i) => i.getAttribute("data-status"))).toEqual(["rejected", "withdrawn"]);
    expect(items[0]).toHaveTextContent("Rejected: not this bean");
    // Not "here": a limit nobody reads is only what was proposed.
    for (const item of items) {
      expect(within(item).getByTestId("override-limit")).toHaveTextContent(
        "Proposed at most 20 % of target (profile: at most 15 % of target)",
      );
      expect(within(item).getByTestId("override-limit")).not.toHaveTextContent("here");
    }
    for (const item of items) expect(within(item).queryAllByRole("button")).toHaveLength(0);
  });

  it("links the conversation that proposed it", async () => {
    renderOverrides([overrideWaiting]);

    expect(await screen.findByTestId("override-thread-link")).toHaveAttribute(
      "href",
      `/chat?thread=${overrideWaiting.proposed_by_thread_id}`,
    );
  });
});

describe("focus", () => {
  it("moves to the override when Confirm, Reject or Withdraw takes its own button away", async () => {
    const user = setupUser();
    renderOverrides([overrideWaiting]);
    const item = await screen.findByTestId("version-override");

    await user.click(within(item).getByTestId("override-confirm"));
    expect(item).toHaveFocus();
    item.blur();

    await user.click(within(item).getByTestId("override-reject"));
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(item).toHaveFocus();
    await user.click(within(item).getByTestId("override-reject"));
    await user.click(screen.getByTestId("reason-submit"));
    expect(item).toHaveFocus();
  });

  it("keeps focus on the override through a double click on Withdraw", async () => {
    const user = setupUser();
    api.withdrawSignatureOverride.mockReturnValue(new Promise(() => undefined));
    renderOverrides([overrideConfirmed]);
    const item = await screen.findByTestId("version-override");

    await user.dblClick(within(item).getByTestId("override-withdraw"));

    expect(api.withdrawSignatureOverride).toHaveBeenCalledTimes(1);
    expect(item).toHaveFocus();
  });

  it("marks Withdraw aria-disabled while the call is out, and never disables it", async () => {
    const user = setupUser();
    api.withdrawSignatureOverride.mockReturnValue(new Promise(() => undefined));
    renderOverrides([overrideConfirmed]);
    const button = await screen.findByTestId("override-withdraw");
    expect(button).toHaveAttribute("aria-disabled", "false");

    await user.click(button);

    await waitFor(() => expect(button).toHaveAttribute("aria-disabled", "true"));
    expect(button).not.toBeDisabled();
  });

  it("moves to the override on Withdraw", async () => {
    const user = setupUser();
    renderOverrides([overrideConfirmed]);
    const item = await screen.findByTestId("version-override");

    await user.click(within(item).getByTestId("override-withdraw"));

    expect(item).toHaveFocus();
  });
});
