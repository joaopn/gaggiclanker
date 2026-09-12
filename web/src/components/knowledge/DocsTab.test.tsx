import { screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { DocsTab } from "@/components/knowledge/DocsTab";
import { knowledgeChunk, knowledgeDoc } from "@/test/analysisFixtures";
import { renderWithQueryClient, setupUser } from "@/test/renderWithQueryClient";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
  Toaster: () => null,
}));

const { getKnowledgeDocs, getKnowledgeDoc, putKnowledgeDoc, resetKnowledgeDoc, searchKnowledge } =
  vi.hoisted(() => ({
    getKnowledgeDocs: vi.fn(),
    getKnowledgeDoc: vi.fn(),
    putKnowledgeDoc: vi.fn(),
    resetKnowledgeDoc: vi.fn(),
    searchKnowledge: vi.fn(),
  }));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  getKnowledgeDocs,
  getKnowledgeDoc,
  putKnowledgeDoc,
  resetKnowledgeDoc,
  searchKnowledge,
}));

const DOCS = {
  items: [
    knowledgeDoc(),
    knowledgeDoc({ id: 2, slug: "PRESSURE_GUIDE", title: "Pressure Guide", edited: true }),
  ],
};

const DETAIL = {
  doc: knowledgeDoc(),
  chunks: [
    knowledgeChunk(),
    knowledgeChunk({
      id: 2,
      heading_path: "ESPRESSO_TASTING_GUIDE#taste-to-cause",
      heading: "Taste to Cause",
      ordinal: 1,
      body: "Salty means severe under-extraction.",
    }),
  ],
};

beforeEach(() => {
  vi.clearAllMocks();
  getKnowledgeDocs.mockResolvedValue(DOCS);
  getKnowledgeDoc.mockResolvedValue(DETAIL);
  putKnowledgeDoc.mockResolvedValue({ ...DETAIL, doc: { ...DETAIL.doc, edited: true } });
  resetKnowledgeDoc.mockResolvedValue(DETAIL);
  searchKnowledge.mockResolvedValue({
    query: "sour",
    items: [
      {
        chunk: knowledgeChunk(),
        score: 4.2,
        snippet: "Sour hits fast and fades; bitter creeps up.",
      },
    ],
  });
});

describe("DocsTab", () => {
  it("lists the documents with what they would cost a prompt", async () => {
    renderWithQueryClient(<DocsTab slug={null} onOpen={vi.fn()} />);

    expect(await screen.findByText("Espresso Tasting Guide")).toBeInTheDocument();
    expect(screen.getByText("Pressure Guide")).toBeInTheDocument();
    // The "edited" badge is how you tell which documents are yours.
    expect(screen.getByText("edited")).toBeInTheDocument();
    expect(screen.getAllByText(/2 chunks · 640 tokens/)).toHaveLength(2);
  });

  it("does not search until there is something worth searching for", async () => {
    const user = setupUser();
    renderWithQueryClient(<DocsTab slug={null} onOpen={vi.fn()} />);

    await user.type(await screen.findByTestId("doc-search"), "s");
    expect(searchKnowledge).not.toHaveBeenCalled();

    await user.type(screen.getByTestId("doc-search"), "our");
    await waitFor(() => expect(searchKnowledge).toHaveBeenCalledWith("sour", 8));
  });

  it("marks the searched words in the snippet", async () => {
    const user = setupUser();
    renderWithQueryClient(<DocsTab slug={null} onOpen={vi.fn()} />);

    await user.type(await screen.findByTestId("doc-search"), "sour");

    const hit = await screen.findByTestId("search-hit");
    expect(hit).toHaveTextContent("ESPRESSO_TASTING_GUIDE#sour-vs-bitter");
    // Both occurrences of the word, and only the word.
    const marks = hit.querySelectorAll("mark");
    expect(marks).toHaveLength(1);
    expect(marks[0]).toHaveTextContent("Sour");
  });

  it("opens a search hit at the passage, not at the top of the file", async () => {
    const user = setupUser();
    const onOpen = vi.fn();
    renderWithQueryClient(<DocsTab slug={null} onOpen={onOpen} />);

    await user.type(await screen.findByTestId("doc-search"), "sour");
    await user.click(await screen.findByText("ESPRESSO_TASTING_GUIDE#sour-vs-bitter"));

    // A hit found one passage in a twenty-chunk file; opening at the top would
    // make the reader find it again by eye.
    expect(onOpen).toHaveBeenCalledWith(
      "ESPRESSO_TASTING_GUIDE",
      "ESPRESSO_TASTING_GUIDE#sour-vs-bitter",
    );
  });

  it("reports a failed search as a failure, not as no results", async () => {
    const user = setupUser();
    searchKnowledge.mockRejectedValue(new Error("the index is rebuilding"));
    renderWithQueryClient(<DocsTab slug={null} onOpen={vi.fn()} />);

    await user.type(await screen.findByTestId("doc-search"), "sour");

    expect(await screen.findByTestId("search-error")).toHaveTextContent("the index is rebuilding");
    expect(screen.queryByText("Nothing matched those words.")).not.toBeInTheDocument();
  });

  it("scrolls the cited chunk into view once it has arrived", async () => {
    const scrollIntoView = vi.fn();
    // jsdom has no layout, so the method does not exist at all.
    Element.prototype.scrollIntoView = scrollIntoView;

    renderWithQueryClient(
      <DocsTab
        slug="ESPRESSO_TASTING_GUIDE"
        onOpen={vi.fn()}
        highlightedChunk="ESPRESSO_TASTING_GUIDE#taste-to-cause"
      />,
    );

    await screen.findAllByTestId("chunk");
    await waitFor(() => expect(scrollIntoView).toHaveBeenCalled());
  });

  it("keeps the heading path out of the element id", async () => {
    renderWithQueryClient(<DocsTab slug="ESPRESSO_TASTING_GUIDE" onOpen={vi.fn()} />);

    const [chunk] = await screen.findAllByTestId("chunk");
    // `#` and `/` are legal in an id and unusable in the selector built from
    // one, and `#` reads as a fragment everywhere else.
    expect(chunk.id).toBe("chunk-ESPRESSO_TASTING_GUIDE-sour-vs-bitter");
    expect(chunk.getAttribute("data-chunk")).toBe("ESPRESSO_TASTING_GUIDE#sour-vs-bitter");
  });

  it("shows the chunks retrieval sees, not only the markdown", async () => {
    renderWithQueryClient(<DocsTab slug="ESPRESSO_TASTING_GUIDE" onOpen={vi.fn()} />);

    const chunks = await screen.findAllByTestId("chunk");
    expect(chunks).toHaveLength(2);
    expect(chunks[0]).toHaveTextContent("ESPRESSO_TASTING_GUIDE#sour-vs-bitter");
    expect(chunks[0]).toHaveTextContent("~320 tokens");
    expect(screen.getByText(/gaggimate-mcp/)).toBeInTheDocument();
  });

  it("marks the chunk a citation linked to", async () => {
    renderWithQueryClient(
      <DocsTab
        slug="ESPRESSO_TASTING_GUIDE"
        onOpen={vi.fn()}
        highlightedChunk="ESPRESSO_TASTING_GUIDE#taste-to-cause"
      />,
    );

    const chunks = await screen.findAllByTestId("chunk");
    expect(chunks[1].className).toContain("border-primary");
    expect(chunks[0].className).not.toContain("border-primary");
  });

  it("saves an edit and re-chunks", async () => {
    const user = setupUser();
    renderWithQueryClient(<DocsTab slug="ESPRESSO_TASTING_GUIDE" onOpen={vi.fn()} />);

    await user.click(await screen.findByTestId("edit-doc"));
    const editor = screen.getByTestId("doc-editor");
    await user.clear(editor);
    await user.type(editor, "# Mine");
    await user.click(screen.getByRole("button", { name: /save and re-chunk/i }));

    await waitFor(() =>
      expect(putKnowledgeDoc).toHaveBeenCalledWith("ESPRESSO_TASTING_GUIDE", "# Mine"),
    );
    // Back to the chunk view, which is what "what will the model see now" means.
    expect(await screen.findAllByTestId("chunk")).toHaveLength(2);
  });

  it("offers a reset only for a document that has been edited", async () => {
    const user = setupUser();
    getKnowledgeDoc.mockResolvedValue({ ...DETAIL, doc: knowledgeDoc({ edited: true }) });
    renderWithQueryClient(<DocsTab slug="ESPRESSO_TASTING_GUIDE" onOpen={vi.fn()} />);

    await user.click(await screen.findByTestId("reset-doc"));
    await waitFor(() => expect(resetKnowledgeDoc).toHaveBeenCalledWith("ESPRESSO_TASTING_GUIDE"));
  });

  it("has no reset button on an unedited document", async () => {
    renderWithQueryClient(<DocsTab slug="ESPRESSO_TASTING_GUIDE" onOpen={vi.fn()} />);

    await screen.findAllByTestId("chunk");
    expect(screen.queryByTestId("reset-doc")).not.toBeInTheDocument();
  });
});
