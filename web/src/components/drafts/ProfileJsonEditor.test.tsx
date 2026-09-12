import { screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ProfileJsonEditor } from "@/components/drafts/ProfileJsonEditor";
import { baseProfile, draft, draftProfile } from "@/test/draftFixtures";
import { renderWithQueryClient, setupUser } from "@/test/renderWithQueryClient";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
  Toaster: () => null,
}));

const { previewProfileDraft, createProfileDraft } = vi.hoisted(() => ({
  previewProfileDraft: vi.fn(),
  createProfileDraft: vi.fn(),
}));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  previewProfileDraft,
  createProfileDraft,
}));

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
  createProfileDraft.mockResolvedValue(draft());
});

function open() {
  return renderWithQueryClient(
    <ProfileJsonEditor
      open
      onOpenChange={() => {}}
      baseVersionId={7}
      label="9 Bar Espresso"
      document={baseProfile()}
    />,
  );
}

describe("ProfileJsonEditor", () => {
  it("validates on the server rather than reimplementing the rules here", async () => {
    // The whole point: both halves of the validation are the same code the push
    // path runs. A second implementation in the browser would eventually
    // disagree with the one that matters.
    open();
    await waitFor(() => expect(previewProfileDraft).toHaveBeenCalled());
    expect(previewProfileDraft.mock.calls[0][0]).toBe(7);
    expect(await screen.findByTestId("editor-valid")).toHaveTextContent("9 Bar Espresso [AI]");
  });

  it("reports a document that is not JSON without asking the server", async () => {
    const user = setupUser();
    open();
    await waitFor(() => expect(previewProfileDraft).toHaveBeenCalledTimes(1));

    await user.clear(screen.getByLabelText("Profile JSON"));
    // `{{` escapes the brace userEvent would otherwise read as a key chord.
    await user.type(screen.getByLabelText("Profile JSON"), "{{not json");

    expect(await screen.findByTestId("editor-json-error")).toBeInTheDocument();
    expect(screen.getByTestId("save-as-draft")).toBeDisabled();
  });

  it("keeps schema errors, policy violations and clamps apart", async () => {
    // Three different problems with three different fixes: it is not a profile,
    // it is a profile the policy refuses, or it is one the policy would move.
    previewProfileDraft.mockResolvedValue({
      valid: false,
      schema_errors: ["phases: List should have at least 1 item"],
      violations: [
        { path: "phases", field: "phases", message: "11 phases, and the policy allows 10" },
      ],
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
      profile: null,
    });
    open();

    expect(await screen.findByTestId("editor-schema-errors")).toHaveTextContent("at least 1 item");
    expect(screen.getByTestId("editor-violations")).toHaveTextContent("policy allows 10");
    expect(screen.getByTestId("editor-clamps")).toHaveTextContent("140 → 100");
    expect(screen.getByTestId("save-as-draft")).toBeDisabled();
  });

  it("saves a valid document as a draft, never straight to the machine", async () => {
    const user = setupUser();
    open();
    await screen.findByTestId("editor-valid");

    await user.click(screen.getByTestId("save-as-draft"));

    await waitFor(() => expect(createProfileDraft).toHaveBeenCalled());
    const body = createProfileDraft.mock.calls[0][0];
    expect(body.base_version_id).toBe(7);
    expect(body.profile.label).toBe("9 Bar Espresso");
    // No analysis, no suggestion: the server takes this path without a provider.
    expect(body.analysis_id).toBeUndefined();
  });
});
