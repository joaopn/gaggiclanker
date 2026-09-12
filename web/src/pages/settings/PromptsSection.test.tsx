import { screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { PromptsSection } from "@/pages/settings/PromptsSection";
import { renderWithQueryClient, setupUser } from "@/test/renderWithQueryClient";

const { toastSuccess, toastError } = vi.hoisted(() => ({
  toastSuccess: vi.fn(),
  toastError: vi.fn(),
}));
vi.mock("sonner", () => ({
  toast: { success: toastSuccess, error: toastError, info: vi.fn() },
  Toaster: () => null,
}));

const { getPrompts, getPrompt, putPrompt, resetPrompt } = vi.hoisted(() => ({
  getPrompts: vi.fn(),
  getPrompt: vi.fn(),
  putPrompt: vi.fn(),
  resetPrompt: vi.fn(),
}));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  getPrompts,
  getPrompt,
  putPrompt,
  resetPrompt,
}));

const PING = "name: ping\nuser: |\n  Say one thing about {{topic}}.\n";

describe("PromptsSection", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getPrompts.mockResolvedValue({
      prompts: [
        {
          name: "ping",
          description: "the self test",
          updated_at: "2026-09-11T00:00:00.000Z",
          edited: false,
          fragment: false,
          valid: true,
          variables: [{ name: "topic", description: "what to remark on" }],
        },
        {
          name: "fragments/style",
          description: "the house voice",
          updated_at: "2026-09-11T00:00:00.000Z",
          edited: true,
          fragment: true,
          valid: true,
          variables: [],
        },
      ],
    });
    getPrompt.mockResolvedValue({
      name: "ping",
      content: PING,
      default_content: PING,
      edited: false,
      updated_at: "2026-09-11T00:00:00.000Z",
      fragment: false,
    });
  });

  it("opens on the first prompt and shows its text and declared variables", async () => {
    renderWithQueryClient(<PromptsSection />);

    const editor = await screen.findByLabelText("Prompt content");
    await waitFor(() => expect(editor).toHaveValue(PING));
    expect(screen.getByText("{{topic}}")).toBeInTheDocument();
    expect(screen.getByText("as shipped")).toBeInTheDocument();
  });

  it("saves an edit and says so", async () => {
    putPrompt.mockResolvedValue({
      name: "ping",
      content: `${PING}# edited\n`,
      default_content: PING,
      edited: true,
      updated_at: "2026-09-11T00:01:00.000Z",
      fragment: false,
    });
    const user = setupUser();
    renderWithQueryClient(<PromptsSection />);

    const editor = await screen.findByLabelText("Prompt content");
    await waitFor(() => expect(editor).toHaveValue(PING));
    await user.type(editor, "# edited");
    await user.click(screen.getByRole("button", { name: /save prompt/i }));

    await waitFor(() => expect(putPrompt).toHaveBeenCalled());
    expect(putPrompt.mock.calls[0][0]).toBe("ping");
    expect(putPrompt.mock.calls[0][1]).toContain("# edited");
  });

  it("keeps what was typed when the server refuses it", async () => {
    // The one moment the user most wants their text back is the moment it did
    // not save.
    putPrompt.mockRejectedValue(new Error("prompt 'ping' is not valid YAML"));
    const user = setupUser();
    renderWithQueryClient(<PromptsSection />);

    const editor = await screen.findByLabelText("Prompt content");
    await waitFor(() => expect(editor).toHaveValue(PING));
    // `[` starts a key descriptor in user-event's mini-language, so the broken
    // YAML is typed without it; what is being asserted is that the draft
    // survives, not which character broke the document.
    await user.type(editor, "oops: - :");
    await user.click(screen.getByRole("button", { name: /save prompt/i }));

    await waitFor(() => expect(toastError).toHaveBeenCalled());
    expect(editor).toHaveValue(`${PING}oops: - :`);
  });

  it("offers reset only for a prompt that has been edited", async () => {
    renderWithQueryClient(<PromptsSection />);

    await screen.findByLabelText("Prompt content");
    expect(screen.getByRole("button", { name: /reset to default/i })).toBeDisabled();
  });

  it("does nothing until the text actually changes", async () => {
    renderWithQueryClient(<PromptsSection />);

    const editor = await screen.findByLabelText("Prompt content");
    await waitFor(() => expect(editor).toHaveValue(PING));
    expect(screen.getByRole("button", { name: /save prompt/i })).toBeDisabled();
  });
});
