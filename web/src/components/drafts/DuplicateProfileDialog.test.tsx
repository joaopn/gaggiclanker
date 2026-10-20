import { screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiClientError } from "@/api/client";
import { DuplicateProfileDialog } from "@/components/drafts/DuplicateProfileDialog";
import { baseProfile, draftProfile } from "@/test/draftFixtures";
import { renderWithQueryClient, setupUser } from "@/test/renderWithQueryClient";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
  Toaster: () => null,
}));

const { previewProfileDraft, duplicateProfile } = vi.hoisted(() => ({
  previewProfileDraft: vi.fn(),
  duplicateProfile: vi.fn(),
}));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  previewProfileDraft,
  duplicateProfile,
}));

const ROW = {
  id: 9,
  label: "Blooming milk",
  current_version_id: 41,
  on_machine: true,
  on_home_screen: true,
  origin: "draft",
};

beforeEach(() => {
  vi.clearAllMocks();
  previewProfileDraft.mockResolvedValue({
    valid: true,
    schema_errors: [],
    violations: [],
    clamp_changes: [],
    stop_condition_changes: [],
    profile: draftProfile(),
  });
  duplicateProfile.mockResolvedValue(ROW);
});

function open(onSaved = vi.fn(), onOpenChange = vi.fn()) {
  renderWithQueryClient(
    <DuplicateProfileDialog
      open
      onOpenChange={onOpenChange}
      baseVersionId={7}
      label="9 Bar Espresso"
      document={baseProfile()}
      onSaved={onSaved}
    />,
  );
  return { onSaved, onOpenChange };
}

describe("DuplicateProfileDialog", () => {
  it("names the original in the title and starts with an empty name and no label in the JSON", async () => {
    open();

    expect(screen.getByText("Duplicate 9 Bar Espresso")).toBeInTheDocument();
    expect(screen.getByTestId("duplicate-profile-name")).toHaveValue("");
    const json = JSON.parse((screen.getByLabelText("Profile JSON") as HTMLTextAreaElement).value);
    expect(json).not.toHaveProperty("label");
    expect(json.type).toBe("standard");
    await screen.findByTestId("editor-valid");
  });

  it("validates on the server rather than reimplementing the rules here", async () => {
    open();
    await waitFor(() => expect(previewProfileDraft).toHaveBeenCalled());
    expect(previewProfileDraft.mock.calls[0][0]).toBe(7);
  });

  it("will not save without a name", async () => {
    const user = setupUser();
    open();
    await screen.findByTestId("editor-valid");

    expect(screen.getByTestId("save-duplicate")).toBeDisabled();
    await user.type(screen.getByTestId("duplicate-profile-name"), "   ");
    expect(screen.getByTestId("save-duplicate")).toBeDisabled();
    await user.type(screen.getByTestId("duplicate-profile-name"), "x");
    expect(screen.getByTestId("save-duplicate")).toBeEnabled();
  });

  it("reports a document that is not JSON without asking the server", async () => {
    const user = setupUser();
    open();
    await waitFor(() => expect(previewProfileDraft).toHaveBeenCalledTimes(1));
    await user.type(screen.getByTestId("duplicate-profile-name"), "Copy");

    await user.clear(screen.getByLabelText("Profile JSON"));
    // `{{` escapes the brace userEvent would otherwise read as a key chord.
    await user.type(screen.getByLabelText("Profile JSON"), "{{not json");

    expect(await screen.findByTestId("editor-json-error")).toBeInTheDocument();
    expect(screen.getByTestId("save-duplicate")).toBeDisabled();
  });

  it("shows what the policy would move, and a clamp blocks the save", async () => {
    const user = setupUser();
    previewProfileDraft.mockResolvedValue({
      valid: true,
      schema_errors: [],
      violations: [],
      clamp_changes: [
        {
          path: "temperature",
          field: "temperature",
          before: 140,
          after: 100,
          reason: "profile temperature must be 60-100 °C",
        },
      ],
      stop_condition_changes: [],
      profile: draftProfile(),
    });
    open();
    await user.type(screen.getByTestId("duplicate-profile-name"), "Copy");

    expect(await screen.findByTestId("editor-clamps")).toHaveTextContent("140 → 100");
    expect(screen.queryByTestId("editor-valid")).not.toBeInTheDocument();
    expect(screen.getByTestId("save-duplicate")).toBeDisabled();
  });

  it("saves under the typed name with the edited document, and hands back the new profile", async () => {
    const user = setupUser();
    const { onSaved, onOpenChange } = open();
    await screen.findByTestId("editor-valid");

    await user.type(screen.getByTestId("duplicate-profile-name"), "  Blooming milk ");
    await user.click(screen.getByTestId("save-duplicate"));

    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(ROW));
    expect(onOpenChange).toHaveBeenCalledWith(false);
    const [versionId, body] = duplicateProfile.mock.calls[0];
    expect(versionId).toBe(7);
    expect(body.name).toBe("Blooming milk");
    expect(body.profile.label).toBe("Blooming milk");
    expect(body.profile.type).toBe("standard");
  });

  it("shows a taken name's sentence under the field and waits for another name", async () => {
    const user = setupUser();
    duplicateProfile.mockRejectedValueOnce(
      new ApiClientError("There is already a profile called 9 Bar Espresso. Choose another name.", {
        status: 409,
        code: "PROFILE_NAME_TAKEN",
        details: { name: "9 bar espresso" },
      }),
    );
    const { onSaved } = open();
    await screen.findByTestId("editor-valid");

    await user.type(screen.getByTestId("duplicate-profile-name"), "9 bar espresso");
    await user.click(screen.getByTestId("save-duplicate"));

    expect(await screen.findByTestId("duplicate-profile-name-taken")).toHaveTextContent(
      "There is already a profile called 9 Bar Espresso. Choose another name.",
    );
    expect(screen.getByTestId("save-duplicate")).toBeDisabled();
    expect(onSaved).not.toHaveBeenCalled();
    await user.type(screen.getByTestId("duplicate-profile-name"), " 2");
    expect(screen.getByTestId("save-duplicate")).toBeEnabled();
  });

  it("sends one request for a double click", async () => {
    const user = setupUser();
    let answer: (row: typeof ROW) => void = () => {};
    duplicateProfile.mockImplementation(
      () =>
        new Promise((resolve) => {
          answer = resolve;
        }),
    );
    open();
    await screen.findByTestId("editor-valid");
    await user.type(screen.getByTestId("duplicate-profile-name"), "Copy");

    await user.dblClick(screen.getByTestId("save-duplicate"));
    answer(ROW);

    await waitFor(() => expect(duplicateProfile).toHaveBeenCalledTimes(1));
  });
});
