import { screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { SetInsights } from "@/components/sets/SetInsights";
import { knowledgeInsight } from "@/test/knowledgeFixtures";
import { renderWithQueryClient, setupUser } from "@/test/renderWithQueryClient";

const {
  getKnowledgeInsights,
  patchKnowledgeInsight,
  dismissKnowledgeInsight,
  deleteKnowledgeInsight,
} = vi.hoisted(() => ({
  getKnowledgeInsights: vi.fn(),
  patchKnowledgeInsight: vi.fn(),
  dismissKnowledgeInsight: vi.fn(),
  deleteKnowledgeInsight: vi.fn(),
}));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  getKnowledgeInsights,
  patchKnowledgeInsight,
  dismissKnowledgeInsight,
  deleteKnowledgeInsight,
}));
vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
  Toaster: () => null,
}));

function own(id: number, label: string | null, over: Parameters<typeof knowledgeInsight>[0] = {}) {
  return knowledgeInsight({
    id,
    text: `Lesson ${id}.`,
    set_id: 3,
    set_version_id: label ? 20 + id : null,
    set_version_label: label,
    general: false,
    scope: {},
    source: "chat",
    confirmed: true,
    evidence_shot_ids: [],
    ...over,
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  getKnowledgeInsights.mockResolvedValue({ items: [], scope_keys: [] });
  patchKnowledgeInsight.mockResolvedValue(knowledgeInsight());
  dismissKnowledgeInsight.mockResolvedValue(knowledgeInsight());
  deleteKnowledgeInsight.mockResolvedValue({ deleted: true });
});

const LABELS = ["v3", "v2", "v1"];

function renderSection() {
  return renderWithQueryClient(<SetInsights setId={3} versionLabels={LABELS} />);
}

describe("SetInsights", () => {
  it("asks for this Set's list and renders nothing when there is nothing", async () => {
    renderSection();
    await vi.waitFor(() => expect(getKnowledgeInsights).toHaveBeenCalledWith({ set_id: 3 }));
    expect(screen.queryByTestId("set-insights")).not.toBeInTheDocument();
  });

  it("groups the Set's own insights by the version they were learned at, newest version first", async () => {
    getKnowledgeInsights.mockResolvedValue({
      items: [own(1, "v1"), own(2, "v3"), own(3, "v1", { confirmed: false }), own(4, null)],
      scope_keys: [],
    });
    renderSection();

    const groups = await screen.findAllByTestId("set-insights-version");
    expect(groups.map((group) => group.getAttribute("data-version"))).toEqual([
      "v3",
      "v1",
      "Version not recorded",
    ]);
    expect(within(groups[0]).getByRole("heading")).toHaveTextContent("Learned at v3");
    expect(within(groups[2]).getByRole("heading")).toHaveTextContent("Version not recorded");
    expect(within(groups[1]).getAllByTestId("own-insight")).toHaveLength(2);
  });

  it("offers Add and Dismiss on a waiting insight and Take back on an added one", async () => {
    getKnowledgeInsights.mockResolvedValue({
      items: [own(1, "v2", { confirmed: false }), own(2, "v2")],
      scope_keys: [],
    });
    renderSection();

    const rows = await screen.findAllByTestId("own-insight");
    expect(rows[0]).toHaveAttribute("data-confirmed", "false");
    expect(rows[0]).toHaveTextContent("waiting");
    expect(rows[0]).toHaveTextContent("not evidence until you add it");
    expect(within(rows[0]).getByRole("button", { name: "Add" })).toBeInTheDocument();
    expect(within(rows[0]).getByRole("button", { name: "Dismiss" })).toBeInTheDocument();
    expect(rows[1]).toHaveAttribute("data-confirmed", "true");
    expect(rows[1]).toHaveTextContent("added");
    expect(within(rows[1]).getByRole("button", { name: "Take back" })).toBeInTheDocument();
    expect(within(rows[1]).queryByRole("button", { name: "Add" })).not.toBeInTheDocument();
  });

  it("adds, dismisses and takes back through the answers the server knows", async () => {
    const user = setupUser();
    getKnowledgeInsights.mockResolvedValue({
      items: [own(1, "v2", { confirmed: false }), own(2, "v2", { confirmed: false }), own(3, "v1")],
      scope_keys: [],
    });
    renderSection();
    const rows = await screen.findAllByTestId("own-insight");

    await user.click(within(rows[0]).getByRole("button", { name: "Add" }));
    await user.click(within(rows[1]).getByRole("button", { name: "Dismiss" }));
    await user.click(within(rows[2]).getByRole("button", { name: "Take back" }));

    expect(patchKnowledgeInsight).toHaveBeenNthCalledWith(1, 1, { confirmed: true });
    expect(dismissKnowledgeInsight).toHaveBeenCalledWith(2);
    expect(patchKnowledgeInsight).toHaveBeenNthCalledWith(2, 3, { confirmed: false });
  });

  it("shows the evidence as links to the shots", async () => {
    getKnowledgeInsights.mockResolvedValue({
      items: [own(1, "v2", { evidence_shot_ids: [4, 9] })],
      scope_keys: [],
    });
    renderSection();
    const row = await screen.findByTestId("own-insight");
    expect(within(row).getByRole("link", { name: "shot 9" })).toHaveAttribute("href", "/shots/9");
  });

  it("lists the general insights that apply beneath, marked general and linking to the Knowledge page", async () => {
    getKnowledgeInsights.mockResolvedValue({
      items: [
        own(1, "v2"),
        knowledgeInsight({
          id: 9,
          text: "Naturals want it finer.",
          scope: { process: "natural" },
          confirmed: true,
          source: "user",
        }),
      ],
      scope_keys: [],
    });
    renderSection();

    const general = await screen.findByTestId("general-insights");
    expect(within(general).getByRole("heading")).toHaveTextContent(
      "General knowledge that applies",
    );
    const row = within(general).getByTestId("general-insight");
    expect(row).toHaveTextContent("general");
    expect(row).toHaveTextContent("process=natural");
    expect(row).toHaveTextContent("Naturals want it finer.");
    expect(within(general).getByRole("link", { name: "Knowledge page" })).toHaveAttribute(
      "href",
      "/knowledge?tab=insights",
    );
    // A general insight is read here, answered on the Knowledge page.
    expect(within(row).queryByRole("button")).not.toBeInTheDocument();
    // And it is not among the Set's own, which are grouped by version.
    expect(screen.getAllByTestId("own-insight")).toHaveLength(1);
  });

  it("shows the general ones alone when the Set has learned nothing of its own", async () => {
    getKnowledgeInsights.mockResolvedValue({
      items: [knowledgeInsight({ confirmed: true })],
      scope_keys: [],
    });
    renderSection();
    expect(await screen.findByTestId("general-insights")).toBeInTheDocument();
    expect(screen.queryByTestId("set-insights-version")).not.toBeInTheDocument();
  });

  it("edits a Set's own insight in place, as the Knowledge page does", async () => {
    const user = setupUser();
    getKnowledgeInsights.mockResolvedValue({ items: [own(1, "v2")], scope_keys: [] });
    patchKnowledgeInsight.mockResolvedValue(own(1, "v2", { text: "Lesson, reworded." }));
    renderSection();

    await user.click(await screen.findByRole("button", { name: "Edit insight 1" }));
    const box = screen.getByLabelText("Text of insight 1");
    await user.clear(box);
    await user.type(box, "Lesson, reworded.");
    await user.click(screen.getByRole("button", { name: "Save" }));

    expect(patchKnowledgeInsight).toHaveBeenCalledWith(1, { text: "Lesson, reworded." });
  });

  it("deletes a Set's own insight", async () => {
    const user = setupUser();
    getKnowledgeInsights.mockResolvedValue({ items: [own(1, "v2")], scope_keys: [] });
    renderSection();

    await user.click(await screen.findByRole("button", { name: "Delete insight 1" }));

    expect(deleteKnowledgeInsight).toHaveBeenCalledWith(1);
  });

  it("offers neither edit nor delete on a general insight, which is the Knowledge page's", async () => {
    getKnowledgeInsights.mockResolvedValue({
      items: [knowledgeInsight({ confirmed: true })],
      scope_keys: [],
    });
    renderSection();
    await screen.findByTestId("general-insight");
    expect(screen.queryByRole("button", { name: /Edit insight|Delete insight/ })).toBeNull();
  });

  it("sends one request however fast the second click comes", async () => {
    const user = setupUser();
    getKnowledgeInsights.mockResolvedValue({
      items: [own(1, "v2", { confirmed: false })],
      scope_keys: [],
    });
    let resolve: (value: unknown) => void = () => undefined;
    patchKnowledgeInsight.mockReturnValue(
      new Promise((done) => {
        resolve = done;
      }),
    );
    renderSection();

    await user.dblClick(await screen.findByRole("button", { name: "Add" }));
    resolve(own(1, "v2"));

    expect(patchKnowledgeInsight).toHaveBeenCalledTimes(1);
  });
});
