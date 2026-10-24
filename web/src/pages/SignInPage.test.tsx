import { createEvent, fireEvent, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { SignInPage } from "@/pages/SignInPage";
import { renderWithQueryClient, setupUser } from "@/test/renderWithQueryClient";

const { getAuthStatus, login, navigate } = vi.hoisted(() => ({
  getAuthStatus: vi.fn(),
  login: vi.fn(),
  navigate: vi.fn(),
}));

vi.mock("@/api/client", () => ({ getAuthStatus, login, logout: vi.fn() }));

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual<typeof import("react-router-dom")>("react-router-dom");
  return { ...actual, useNavigate: () => navigate };
});

describe("SignInPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getAuthStatus.mockResolvedValue({ auth_required: true, authenticated: false, user: null });
  });

  it("signs in and goes where the user was headed", async () => {
    login.mockResolvedValue({ token: "t", expires_in: 60, user: "barista" });
    const user = setupUser();
    const { getByLabelText, getByRole } = renderWithQueryClient(<SignInPage />, {
      initialEntries: ["/sign-in?next=/recipes/3"],
    });

    await user.type(getByLabelText("Username"), "barista");
    await user.type(getByLabelText("Password"), "secret");
    await user.click(getByRole("button", { name: "Sign in" }));

    await waitFor(() => expect(login).toHaveBeenCalledWith("barista", "secret"));
    await waitFor(() => expect(navigate).toHaveBeenCalledWith("/recipes/3", { replace: true }));
  });

  it("falls back to the shots list when there is no next path", async () => {
    login.mockResolvedValue({ token: "t", expires_in: 60, user: "barista" });
    const user = setupUser();
    const { getByLabelText, getByRole } = renderWithQueryClient(<SignInPage />, {
      initialEntries: ["/sign-in"],
    });

    await user.type(getByLabelText("Username"), "barista");
    await user.type(getByLabelText("Password"), "secret");
    await user.click(getByRole("button", { name: "Sign in" }));

    await waitFor(() => expect(navigate).toHaveBeenCalledWith("/shots", { replace: true }));
  });

  it("shows the server's message and stays put when the credentials are wrong", async () => {
    login.mockRejectedValue(new Error("Invalid username or password"));
    const user = setupUser();
    const { getByLabelText, getByRole, findByRole } = renderWithQueryClient(<SignInPage />, {
      initialEntries: ["/sign-in"],
    });

    await user.type(getByLabelText("Username"), "barista");
    await user.type(getByLabelText("Password"), "wrong");
    await user.click(getByRole("button", { name: "Sign in" }));

    expect(await findByRole("alert")).toHaveTextContent("Invalid username or password");
    expect(navigate).not.toHaveBeenCalled();
  });

  it("leaves the page when the server has auth switched off", async () => {
    getAuthStatus.mockResolvedValue({ auth_required: false, authenticated: true, user: null });
    renderWithQueryClient(<SignInPage />, { initialEntries: ["/sign-in"] });
    await waitFor(() => expect(navigate).toHaveBeenCalledWith("/shots", { replace: true }));
  });

  it("does not submit the password as a query parameter", async () => {
    // A GET form would put the password in the URL, the history and every
    // proxy log between here and the box. The form has no method attribute on
    // purpose and the submit handler preventDefaults.
    const { container } = renderWithQueryClient(<SignInPage />, { initialEntries: ["/sign-in"] });
    const form = container.querySelector("form");
    expect(form?.getAttribute("method")).toBeNull();
    expect(container.querySelector('input[name="password"]')).toHaveAttribute("type", "password");
  });

  it("handles the submit itself, so the browser never sends the form as a GET", async () => {
    // Without preventDefault a form with no method submits as a GET, and the
    // password lands in the URL.
    login.mockResolvedValue({ token: "t", expires_in: 60, user: "barista" });
    const user = setupUser();
    const { container, getByLabelText } = renderWithQueryClient(<SignInPage />, {
      initialEntries: ["/sign-in"],
    });
    await user.type(getByLabelText("Username"), "barista");
    await user.type(getByLabelText("Password"), "secret");

    const form = container.querySelector("form") as HTMLFormElement;
    const submit = createEvent.submit(form);
    fireEvent(form, submit);

    expect(submit.defaultPrevented).toBe(true);
    await waitFor(() => expect(login).toHaveBeenCalledWith("barista", "secret"));
  });
});
