import { fireEvent, render, screen, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { knowledgeHref } from "@/components/chat/citations";
import { AnswerText, InlineMarkdown } from "@/components/chat/markdown";

function renderAnswer(text: string) {
  return render(
    <MemoryRouter>
      <AnswerText text={text} />
    </MemoryRouter>,
  );
}

describe("AnswerText renders markdown", () => {
  it("renders bold as a strong element", () => {
    const { container } = renderAnswer("Grind **finer** next time.");
    expect(container.querySelector("strong")?.textContent).toBe("finer");
    expect(container.textContent).not.toContain("**");
  });

  it("renders inline code as a code element", () => {
    const { container } = renderAnswer("Set `grind 12` today.");
    expect(container.querySelector("code")?.textContent).toBe("grind 12");
    expect(container.textContent).not.toContain("`");
  });

  it("renders a heading", () => {
    renderAnswer("## Next steps\n\nGrind finer.");
    expect(screen.getByRole("heading", { name: "Next steps" })).toBeTruthy();
  });

  it("renders a numbered list as an ol", () => {
    const { container } = renderAnswer("1. First\n2. Second");
    expect(container.querySelectorAll("ol > li")).toHaveLength(2);
  });

  it("renders a GFM table", () => {
    const { container } = renderAnswer("| Shot | Yield |\n|---|---|\n| 1 | 36 |\n| 2 | 38 |");
    const cells = [...container.querySelectorAll("td")].map((cell) => cell.textContent);
    expect(cells).toEqual(["1", "36", "2", "38"]);
    expect(container.querySelectorAll("th")).toHaveLength(2);
    expect(container.textContent).not.toContain("|");
  });
});

describe("citations inside markdown", () => {
  function hrefs(container: HTMLElement) {
    return [...container.querySelectorAll("a")].map((a) => a.getAttribute("href"));
  }
  const PATH = "ESPRESSO_TASTING_GUIDE#sour-vs-bitter";
  const PATH_HREF = knowledgeHref(PATH);

  it("links a citation in plain text", () => {
    const { container } = renderAnswer("Compare with shot 12 please.");
    expect(hrefs(container)).toEqual(["/shots/12"]);
    expect(container.querySelector("a")?.textContent).toBe("shot 12");
  });

  it("links a heading path in plain text, in monospace", () => {
    const { container } = renderAnswer(`See ${PATH} for more.`);
    expect(hrefs(container)).toEqual([PATH_HREF]);
    expect(container.querySelector("a")?.className).toContain("font-mono");
  });

  it("links a citation inside bold", () => {
    const { container } = renderAnswer("**see shot 12**");
    expect(container.querySelector("strong a")?.getAttribute("href")).toBe("/shots/12");
  });

  it("links a citation inside a list item", () => {
    const { container } = renderAnswer("- one\n- shot 12 ran fast");
    expect(container.querySelector("li a")?.getAttribute("href")).toBe("/shots/12");
  });

  it("links a citation inside a table cell", () => {
    const { container } = renderAnswer("| a | b |\n|---|---|\n| shot 12 | x |");
    expect(container.querySelector("td a")?.getAttribute("href")).toBe("/shots/12");
  });

  it("links a citation inside a heading", () => {
    renderAnswer("## About shot 12");
    expect(within(screen.getByRole("heading")).getByRole("link").getAttribute("href")).toBe(
      "/shots/12",
    );
  });

  it("does not link inside a fenced block", () => {
    const { container } = renderAnswer(["```", `shot 12 and ${PATH}`, "```"].join("\n"));
    expect(container.querySelectorAll("a")).toHaveLength(0);
    expect(container.querySelector("pre")?.textContent).toBe(`shot 12 and ${PATH}`);
  });

  it("does not link inline code that merely contains a citation", () => {
    const { container } = renderAnswer("Use `shot 12 at 9 bar` here.");
    expect(container.querySelectorAll("a")).toHaveLength(0);
    expect(container.querySelector("code")?.textContent).toBe("shot 12 at 9 bar");
  });

  it("links inline code that is exactly one citation", () => {
    const { container } = renderAnswer(`See \`${PATH}\` and \`shot 7\`.`);
    expect(hrefs(container)).toEqual([PATH_HREF, "/shots/7"]);
    expect(container.querySelector("a")?.className).toContain("font-mono");
  });

  it("does not nest an anchor in a markdown link's text", () => {
    const { container } = renderAnswer("[look at shot 12](https://example.com)");
    expect(container.querySelectorAll("a")).toHaveLength(1);
    expect(container.querySelector("a")?.getAttribute("href")).toBe("https://example.com");
  });
});

describe("links", () => {
  it("renders an in-app path as a router link", () => {
    const { container } = renderAnswer("[the set](/sets/3)");
    const link = container.querySelector("a");
    expect(link?.getAttribute("href")).toBe("/sets/3");
    expect(link?.hasAttribute("target")).toBe(false);
  });

  it("opens an external link in a new tab without the opener", () => {
    const { container } = renderAnswer("[docs](https://example.com)");
    const link = container.querySelector("a");
    expect(link?.getAttribute("target")).toBe("_blank");
    expect(link?.getAttribute("rel")).toContain("noopener");
  });

  it("treats a protocol-relative address as external", () => {
    const { container } = renderAnswer("[x](//example.com/a)");
    expect(container.querySelector("a")?.getAttribute("target")).toBe("_blank");
  });

  it("drops a javascript: href", () => {
    const { container } = renderAnswer("[x](javascript:alert(1))");
    for (const a of container.querySelectorAll("a")) {
      expect(a.getAttribute("href") ?? "").not.toMatch(/^javascript:/i);
    }
  });
});

describe("raw HTML and images", () => {
  it("never makes an element from HTML in the text", () => {
    const { container } = renderAnswer(
      "<script>alert(1)</script>\n\n<img src=x onerror=alert(1)>\n\n<b>hi</b>",
    );
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("b")).toBeNull();
  });

  it("renders an image as a link to it, never an img", () => {
    const { container } = renderAnswer("![the puck](https://example.com/a.png)");
    expect(container.querySelector("img")).toBeNull();
    const link = container.querySelector("a");
    expect(link?.getAttribute("href")).toBe("https://example.com/a.png");
    expect(link?.textContent).toBe("the puck");
  });

  it("uses the URL as the link text when the image has no alt", () => {
    const { container } = renderAnswer("![](https://example.com/a.png)");
    expect(container.querySelector("a")?.textContent).toBe("https://example.com/a.png");
  });

  it("does not nest an anchor for an image inside a link", () => {
    const { container } = renderAnswer("[![alt](https://example.com/a.png)](https://example.com)");
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelectorAll("a")).toHaveLength(1);
  });
});

describe("blocks", () => {
  it("keeps a single newline as a line break inside one paragraph", () => {
    const { container } = renderAnswer("line one\nline two");
    expect(container.querySelectorAll("p")).toHaveLength(1);
    expect(container.querySelector("p br")).not.toBeNull();
  });

  it("nests a list inside a list item", () => {
    const { container } = renderAnswer("- a\n  - b\n  - c\n- d");
    expect(container.querySelectorAll("ul > li > ul > li")).toHaveLength(2);
  });

  it("renders a task list with disabled checkboxes", () => {
    const { container } = renderAnswer("- [x] done\n- [ ] todo");
    const boxes = container.querySelectorAll<HTMLInputElement>("input[type=checkbox]");
    expect(boxes).toHaveLength(2);
    expect(boxes[0].checked).toBe(true);
    expect(boxes[0].disabled).toBe(true);
    expect(boxes[1].disabled).toBe(true);
    // No bullets beside the checkboxes.
    expect(container.querySelector("ul")?.className).toContain("list-none");
  });

  it("renders strikethrough, a quote and a rule", () => {
    const { container } = renderAnswer("~~old~~\n\n> quoted\n\n---");
    expect(container.querySelector("del")?.textContent).toBe("old");
    expect(container.querySelector("blockquote")?.textContent).toContain("quoted");
    expect(container.querySelector("hr")).not.toBeNull();
  });

  it("honours table column alignment", () => {
    const { container } = renderAnswer("| a | b | c |\n|:-:|--:|---|\n| 1 | 2 | 3 |");
    const cells = container.querySelectorAll<HTMLElement>("td");
    expect(cells[0].style.textAlign).toBe("center");
    expect(cells[1].style.textAlign).toBe("right");
    expect(cells[2].style.textAlign).toBe("");
  });

  it("scrolls a table inside its own wrapper", () => {
    const { container } = renderAnswer("| a |\n|---|\n| 1 |");
    expect(container.querySelector("table")?.parentElement?.className).toContain("overflow-x-auto");
  });

  it("does not style fenced code as inline code", () => {
    const { container } = renderAnswer("```sql\nselect 1\n```");
    expect(container.querySelector("pre code")).toBeNull();
    expect(container.querySelector("pre")?.textContent).toBe("select 1");
  });

  it("renders partial markdown while an answer streams", () => {
    expect(() => renderAnswer("An **unclosed bold and `code")).not.toThrow();
    expect(() => renderAnswer("| a | b |\n|---|")).not.toThrow();
    expect(() => renderAnswer("| a | b |\n|---|---|\n| 1")).not.toThrow();
    expect(() => renderAnswer("```sql\nselect")).not.toThrow();
  });
});

describe("InlineMarkdown", () => {
  function renderInline(text: string) {
    return render(
      <MemoryRouter>
        <p>
          <InlineMarkdown text={text} />
        </p>
      </MemoryRouter>,
    );
  }

  it("renders bold, italics and code", () => {
    const { container } = renderInline("A **bold** and *soft* `code`");
    expect(container.querySelector("strong")?.textContent).toBe("bold");
    expect(container.querySelector("em")?.textContent).toBe("soft");
    expect(container.querySelector("code")?.textContent).toBe("code");
  });

  it("links citations", () => {
    const { container } = renderInline("see shot 12");
    expect(container.querySelector("a")?.getAttribute("href")).toBe("/shots/12");
  });

  it("adds no block elements: a heading and a list keep their words only", () => {
    const { container } = renderInline("# Title\n\n- one\n- two");
    for (const tag of ["h1", "ul", "li", "ol"]) expect(container.querySelector(tag)).toBeNull();
    expect(container.querySelectorAll("p")).toHaveLength(1);
    expect(container.textContent).toContain("Title");
    expect(container.textContent).toMatch(/one\s+two/);
  });
});

describe("review fixes", () => {
  it("keeps an ordered list's start number", () => {
    const { container } = renderAnswer("3. third\n4. fourth");
    expect(container.querySelector("ol")?.getAttribute("start")).toBe("3");
  });

  it("continues a numbered list a fence interrupted", () => {
    const { container } = renderAnswer("1. one\n2. two\n\n```\nx\n```\n\n3. three");
    const lists = container.querySelectorAll("ol");
    expect(lists).toHaveLength(2);
    expect(lists[1].getAttribute("start")).toBe("3");
  });

  it("does not nest an anchor for an image inside a reference link", () => {
    const text = "[![a](https://e.com/a.png)][r]\n\n[r]: https://y.com";
    const errors = vi.spyOn(console, "error").mockImplementation(() => {});
    const { container } = renderAnswer(text);
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelectorAll("a")).toHaveLength(1);
    const inline = render(
      <MemoryRouter>
        <InlineMarkdown text={text} />
      </MemoryRouter>,
    );
    expect(inline.container.querySelectorAll("a")).toHaveLength(1);
    // React reports nested anchors as a validateDOMNesting error.
    expect(errors).not.toHaveBeenCalled();
    errors.mockRestore();
  });

  it("does not nest an anchor for a citation in a reference link's text", () => {
    const { container } = renderAnswer("[look at shot 12][r]\n\n[r]: https://y.com");
    expect(container.querySelectorAll("a")).toHaveLength(1);
    expect(container.querySelector("a")?.getAttribute("href")).toBe("https://y.com");
  });

  it("links a citation inside a blockquote and inside emphasis", () => {
    const { container } = renderAnswer("> as in shot 12\n\n*and shot 13*");
    expect(container.querySelector("blockquote a")?.getAttribute("href")).toBe("/shots/12");
    expect(container.querySelector("em a")?.getAttribute("href")).toBe("/shots/13");
  });

  it("shows only the words of a link with a blocked scheme", () => {
    for (const url of [
      "javascript:alert(1)",
      "data:text/html,x",
      "vbscript:x",
      "file:///etc/passwd",
    ]) {
      const { container } = renderAnswer(`[click me](${url})`);
      expect(container.querySelector("a")).toBeNull();
      expect(container.textContent).toContain("click me");
    }
  });

  it("keeps a footnote on the same page and hides its label", () => {
    const { container } = renderAnswer("Text[^1].\n\n[^1]: The note.");
    const ref = container.querySelector("a[data-footnote-ref]");
    expect(ref?.getAttribute("href")).toMatch(/^#/);
    expect(ref?.hasAttribute("target")).toBe(false);
    const target = ref?.getAttribute("href")?.slice(1) ?? "";
    expect(container.querySelector(`[id="${target}"]`)).not.toBeNull();
    const label = screen.getByRole("heading", { name: "Footnotes" });
    expect(label.className).toContain("sr-only");
    for (const a of container.querySelectorAll("a[data-footnote-backref]")) {
      expect(a.hasAttribute("target")).toBe(false);
    }
  });

  it("gives two answers with footnotes no duplicate ids", () => {
    const note = "Text[^1].\n\n[^1]: The note.";
    const { container } = render(
      <MemoryRouter>
        <AnswerText text={note} />
        <AnswerText text={note} />
      </MemoryRouter>,
    );
    const ids = [...container.querySelectorAll("[id]")].map((el) => el.id);
    expect(ids.length).toBeGreaterThan(0);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it("wraps a long unbroken word in the answer but not inside tables", () => {
    const { container } = renderAnswer("| a |\n|---|\n| 1 |");
    expect(container.firstElementChild?.className).toContain("[overflow-wrap:anywhere]");
    expect(container.querySelector("table")?.className).toContain("[overflow-wrap:normal]");
  });

  it("wraps long inline output too", () => {
    const { container } = render(
      <MemoryRouter>
        <InlineMarkdown text="x" />
      </MemoryRouter>,
    );
    expect(container.firstElementChild?.className).toContain("[overflow-wrap:anywhere]");
  });

  describe("InlineMarkdown", () => {
    function inline(text: string) {
      return render(
        <MemoryRouter>
          <p>
            <InlineMarkdown text={text} />
          </p>
        </MemoryRouter>,
      );
    }

    it("renders a citation as a router link", () => {
      const { container } = inline("see shot 12");
      const link = container.querySelector("a");
      expect(link?.getAttribute("href")).toBe("/shots/12");
      expect(link?.hasAttribute("target")).toBe(false);
    });

    it("opens an external link in a new tab without the opener", () => {
      const { container } = inline("[docs](https://example.com)");
      const link = container.querySelector("a");
      expect(link?.getAttribute("target")).toBe("_blank");
      expect(link?.getAttribute("rel")).toContain("noopener");
    });

    it("shows no footnotes section and no label", () => {
      const { container } = inline("Text[^1].\n\n[^1]: The note.");
      expect(container.textContent).not.toContain("Footnotes");
      expect(container.textContent).not.toContain("The note");
      expect(container.querySelector("section, ol, sup, a")).toBeNull();
      expect(container.textContent).toContain("Text[1].");
    });

    it("shows only the words of a blocked-scheme link", () => {
      const { container } = inline("[click me](javascript:alert(1))");
      expect(container.querySelector("a")).toBeNull();
      expect(container.textContent?.trim()).toBe("click me");
    });
  });
});

describe("in-app links navigate without a page load", () => {
  // jsdom renders a router `Link` and a plain `<a href>` identically, so only a
  // click can tell them apart: the router's click moves the location, a plain
  // anchor's click does nothing here and would reload the page in a browser.
  function Where() {
    return <p data-testid="where">{useLocation().pathname}</p>;
  }

  function renderAt(node: ReactNode) {
    return render(
      <MemoryRouter initialEntries={["/chat"]}>
        <Routes>
          <Route
            path="*"
            element={
              <>
                {node}
                <Where />
              </>
            }
          />
        </Routes>
      </MemoryRouter>,
    );
  }

  it("follows a citation in an answer through the router", () => {
    renderAt(<AnswerText text="Compare shot 12." />);
    fireEvent.click(screen.getByRole("link", { name: "shot 12" }));
    expect(screen.getByTestId("where")).toHaveTextContent("/shots/12");
  });

  it("follows a citation in a card's text through the router", () => {
    renderAt(<InlineMarkdown text="**Finer**, see shot 12." />);
    fireEvent.click(screen.getByRole("link", { name: "shot 12" }));
    expect(screen.getByTestId("where")).toHaveTextContent("/shots/12");
  });
});
