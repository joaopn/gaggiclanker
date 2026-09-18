import { screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { findSettingsPage, type SettingsPageInfo } from "@/lib/settingsPages";
import { PromptsPage } from "@/pages/settings/PromptsPage";
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

const PAGE = findSettingsPage("prompts") as SettingsPageInfo;

const PING = "name: ping\nuser: |\n  Say one thing about {{topic}}.\n";

/** Render the page and open the `ping` card, as a person would. */
async function openPing(user = setupUser()) {
  renderWithQueryClient(<PromptsPage page={PAGE} />);
  await user.click(await screen.findByRole("button", { name: "ping" }));
  const editor = await screen.findByLabelText("Content of ping");
  await waitFor(() => expect(editor).toHaveValue(PING));
  return { user, editor };
}

describe("PromptsPage", () => {
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

  it("lists every prompt as a closed card, and fetches none of their texts until one opens", async () => {
    renderWithQueryClient(<PromptsPage page={PAGE} />);

    const ping = await screen.findByRole("button", { name: "ping" });
    expect(ping).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByRole("button", { name: "fragments/style" })).toHaveAttribute(
      "aria-expanded",
      "false",
    );
    // What a closed card still says: the summary, and whether it was edited.
    expect(screen.getByText("the self test")).toBeInTheDocument();
    expect(screen.getByText("edited - no longer tracks the shipped version")).toBeInTheDocument();
    expect(getPrompt).not.toHaveBeenCalled();
  });

  it("opens a prompt's card on its text and declared variables", async () => {
    await openPing();
    expect(getPrompt).toHaveBeenCalledWith("ping");
    expect(screen.getByText("{{topic}}")).toBeInTheDocument();
  });

  it("opens the card a link names", async () => {
    renderWithQueryClient(<PromptsPage page={PAGE} />, {
      initialEntries: ["/settings/prompts#ping"],
    });
    expect(await screen.findByLabelText("Content of ping")).toBeVisible();
    expect(screen.getByRole("button", { name: "ping" })).toHaveAttribute("aria-expanded", "true");
  });

  it("ignores a hash that does not decode", async () => {
    renderWithQueryClient(<PromptsPage page={PAGE} />, {
      initialEntries: ["/settings/prompts#%E0%A4%A"],
    });
    expect(await screen.findByRole("button", { name: "ping" })).toHaveAttribute(
      "aria-expanded",
      "false",
    );
  });

  it("keeps an unsaved draft when its card is closed and opened again", async () => {
    const { user, editor } = await openPing();
    await user.type(editor, "# half done");
    await user.click(screen.getByRole("button", { name: "ping" }));
    expect(editor).not.toBeVisible();
    await user.click(screen.getByRole("button", { name: "ping" }));
    expect(editor).toHaveValue(`${PING}# half done`);
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
    const { user, editor } = await openPing();

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
    const { user, editor } = await openPing();

    // `[` starts a key descriptor in user-event's mini-language, so the broken
    // YAML is typed without it; what is being asserted is that the draft
    // survives, not which character broke the document.
    await user.type(editor, "oops: - :");
    await user.click(screen.getByRole("button", { name: /save prompt/i }));

    await waitFor(() => expect(toastError).toHaveBeenCalled());
    expect(editor).toHaveValue(`${PING}oops: - :`);
  });

  it("offers reset only for a prompt that has been edited", async () => {
    const { editor } = await openPing();
    const card = editor.closest("[data-slot=card]") as HTMLElement;
    expect(within(card).getByRole("button", { name: /reset to default/i })).toBeDisabled();
  });

  it("does nothing until the text actually changes", async () => {
    await openPing();
    expect(screen.getByRole("button", { name: /save prompt/i })).toBeDisabled();
  });
});
