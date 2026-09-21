import { describe, expect, it } from "vitest";
import { contentMaxWidth, WIDE_MAX_WIDTH } from "@/lib/navigation";

/**
 * Which pages are laid out as a dataset and which as something to read. The
 * shell asks this on every render, so it is a string in, a class out.
 */
describe("contentMaxWidth", () => {
  it("gives the archive the window, less the shell's gutters and a cap", () => {
    expect(contentMaxWidth("/shots")).toBe(WIDE_MAX_WIDTH);
    // A trailing slash is the same route to the router, so it is here too.
    expect(contentMaxWidth("/shots/")).toBe(WIDE_MAX_WIDTH);
  });

  it("keeps a reading column everywhere else, one shot included", () => {
    // A shot is a page of prose, charts and a form; only the list is a table.
    for (const path of ["/shots/129", "/sets", "/chat", "/settings/llm", "/nope", "/"]) {
      expect(contentMaxWidth(path), path).toBe("max-w-5xl");
    }
  });
});
