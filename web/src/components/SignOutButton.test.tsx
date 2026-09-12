import { waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { SignOutButton } from "@/components/SignOutButton";
import {
  createTestQueryClient,
  renderWithQueryClient,
  setupUser,
} from "@/test/renderWithQueryClient";

const { getAuthStatus, logout, navigate } = vi.hoisted(() => ({
  getAuthStatus: vi.fn(),
  logout: vi.fn(),
  navigate: vi.fn(),
}));

vi.mock("@/api/client", () => ({ getAuthStatus, logout, login: vi.fn() }));

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual<typeof import("react-router-dom")>("react-router-dom");
  return { ...actual, useNavigate: () => navigate };
});

describe("SignOutButton", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    logout.mockResolvedValue(undefined);
  });

  it("renders nothing on a server with auth switched off", async () => {
    getAuthStatus.mockResolvedValue({ auth_required: false, authenticated: true, user: null });
    const { queryByRole } = renderWithQueryClient(<SignOutButton />);
    await waitFor(() => expect(getAuthStatus).toHaveBeenCalled());
    expect(queryByRole("button", { name: "Sign out" })).toBeNull();
  });

  it("revokes the session and returns to the sign-in page", async () => {
    getAuthStatus.mockResolvedValue({
      auth_required: true,
      authenticated: true,
      user: "barista",
    });
    const user = setupUser();
    const queryClient = createTestQueryClient();
    // Nothing from the old session may be left to flash on the way out. Spied
    // rather than asserted on the cache's contents, because this component's
    // own status query re-subscribes the moment the clear finishes.
    const clear = vi.spyOn(queryClient, "clear");
    const { findByRole } = renderWithQueryClient(<SignOutButton />, { queryClient });

    await user.click(await findByRole("button", { name: "Sign out" }));

    await waitFor(() => expect(logout).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(navigate).toHaveBeenCalledWith("/sign-in", { replace: true }));
    expect(clear).toHaveBeenCalled();
  });

  it("still signs out locally when the server call fails", async () => {
    getAuthStatus.mockResolvedValue({
      auth_required: true,
      authenticated: true,
      user: "barista",
    });
    logout.mockRejectedValue(new Error("gone"));
    const user = setupUser();
    const { findByRole } = renderWithQueryClient(<SignOutButton />);

    await user.click(await findByRole("button", { name: "Sign out" }));

    // `onSettled`, not `onSuccess`: a button that leaves you signed in because
    // the token had already expired is the worst of both.
    await waitFor(() => expect(navigate).toHaveBeenCalledWith("/sign-in", { replace: true }));
  });
});
