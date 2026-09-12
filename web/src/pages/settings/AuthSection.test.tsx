import { waitFor } from "@testing-library/react";
import { useForm } from "react-hook-form";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ResolvedSetting, SettingsMap } from "@/api/types";
import { AuthSection } from "@/pages/settings/AuthSection";
import {
  buildSettingsSchema,
  isEditable,
  type SettingsFormValues,
  toFormValues,
  toPatch,
} from "@/pages/settings/schema";
import { renderWithQueryClient, setupUser } from "@/test/renderWithQueryClient";

const { getAuthStatus, setPassword, navigate } = vi.hoisted(() => ({
  getAuthStatus: vi.fn(),
  setPassword: vi.fn(),
  navigate: vi.fn(),
}));

vi.mock("@/api/client", () => ({
  getAuthStatus,
  setPassword,
  login: vi.fn(),
  logout: vi.fn(),
  clearAuthSession: vi.fn(),
}));

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual<typeof import("react-router-dom")>("react-router-dom");
  return { ...actual, useNavigate: () => navigate };
});

function hashSetting(configured: boolean): ResolvedSetting {
  return {
    key: "authPasswordHash",
    type: "string",
    secret: true,
    readonly: true,
    configured,
    hint: configured ? "$arg" : null,
    source: configured ? "database" : "default",
    description: "argon2id hash of the sign-in password.",
  };
}

const userSetting: ResolvedSetting = {
  key: "authUser",
  type: "string",
  secret: false,
  readonly: false,
  value: "",
  default: "",
  override: null,
  source: "default",
  description: "Sign-in username.",
};

function Harness({ entries }: { entries: ResolvedSetting[] }) {
  const form = useForm<SettingsFormValues>({ defaultValues: {} });
  return <AuthSection entries={entries} control={form.control} errors={form.formState.errors} />;
}

describe("AuthSection", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getAuthStatus.mockResolvedValue({ auth_required: false, authenticated: true, user: null });
    setPassword.mockResolvedValue({ auth_required: false, sessions_revoked: true });
  });

  it("never renders an editable field for the hash", () => {
    const { queryByLabelText, getByLabelText } = renderWithQueryClient(
      <Harness entries={[userSetting, hashSetting(true)]} />,
    );
    // The whole reason this component exists: a masked box labelled "Auth
    // password hash" invites typing the password into it.
    expect(queryByLabelText("Auth password hash")).toBeNull();
    expect(getByLabelText("Auth user")).toBeInTheDocument();
    expect(getByLabelText("Sign-in password")).toBeInTheDocument();
  });

  it("says whether a password is set", () => {
    const { getByText, rerender } = renderWithQueryClient(
      <Harness entries={[hashSetting(false)]} />,
    );
    expect(getByText("not set")).toBeInTheDocument();
    rerender(<Harness entries={[hashSetting(true)]} />);
    expect(getByText("set")).toBeInTheDocument();
  });

  it("sends the plain password, and no hash, to the password endpoint", async () => {
    const user = setupUser();
    const { getByLabelText, getByRole } = renderWithQueryClient(
      <Harness entries={[hashSetting(false)]} />,
    );

    await user.type(getByLabelText("New password"), "a-long-enough-passphrase");
    await user.type(getByLabelText("Repeat it"), "a-long-enough-passphrase");
    await user.click(getByRole("button", { name: "Set password" }));

    // The first argument only: TanStack passes the mutation context as a second.
    await waitFor(() =>
      expect(setPassword.mock.calls[0]?.[0]).toEqual({
        newPassword: "a-long-enough-passphrase",
      }),
    );
  });

  it("asks for the current password when one is already set", async () => {
    const user = setupUser();
    const { getByLabelText, getByRole } = renderWithQueryClient(
      <Harness entries={[hashSetting(true)]} />,
    );

    await user.type(getByLabelText("Current password"), "the-old-one");
    await user.type(getByLabelText("New password"), "a-long-enough-passphrase");
    await user.type(getByLabelText("Repeat it"), "a-long-enough-passphrase");
    await user.click(getByRole("button", { name: "Change password" }));

    await waitFor(() =>
      expect(setPassword.mock.calls[0]?.[0]).toEqual({
        currentPassword: "the-old-one",
        newPassword: "a-long-enough-passphrase",
      }),
    );
  });

  it("refuses a mistyped repeat before anything reaches the server", async () => {
    const user = setupUser();
    const { getByLabelText, getByRole, findByRole } = renderWithQueryClient(
      <Harness entries={[hashSetting(false)]} />,
    );

    await user.type(getByLabelText("New password"), "a-long-enough-passphrase");
    await user.type(getByLabelText("Repeat it"), "a-long-enough-passphrasf");
    await user.click(getByRole("button", { name: "Set password" }));

    expect(await findByRole("alert")).toHaveTextContent("do not match");
    expect(setPassword).not.toHaveBeenCalled();
  });

  it("refuses a short password before anything reaches the server", async () => {
    const user = setupUser();
    const { getByLabelText, getByRole, findByRole } = renderWithQueryClient(
      <Harness entries={[hashSetting(false)]} />,
    );

    await user.type(getByLabelText("New password"), "short");
    await user.type(getByLabelText("Repeat it"), "short");
    await user.click(getByRole("button", { name: "Set password" }));

    expect(await findByRole("alert")).toHaveTextContent("At least 12");
    expect(setPassword).not.toHaveBeenCalled();
  });

  it("shows the server's refusal rather than swallowing it", async () => {
    setPassword.mockRejectedValue(new Error("The current password is wrong"));
    const user = setupUser();
    const { getByLabelText, getByRole, findByRole } = renderWithQueryClient(
      <Harness entries={[hashSetting(true)]} />,
    );

    await user.type(getByLabelText("Current password"), "nope");
    await user.type(getByLabelText("New password"), "a-long-enough-passphrase");
    await user.type(getByLabelText("Repeat it"), "a-long-enough-passphrase");
    await user.click(getByRole("button", { name: "Change password" }));

    expect(await findByRole("alert")).toHaveTextContent("The current password is wrong");
  });
});

describe("the generated form leaves the hash alone", () => {
  const settings: SettingsMap = {
    authUser: userSetting,
    authPasswordHash: hashSetting(true),
  };

  it("excludes it from the form's values, schema and patch", () => {
    expect(isEditable(settings.authPasswordHash)).toBe(false);
    expect(toFormValues(settings)).toEqual({ authUser: "" });
    expect(Object.keys(buildSettingsSchema(settings).parse({ authUser: "x" }))).toEqual([
      "authUser",
    ]);
    // Even if something put a value in the form state, it does not get sent.
    expect(toPatch(settings, { authUser: "", authPasswordHash: "hunter2" })).toEqual({});
  });
});
