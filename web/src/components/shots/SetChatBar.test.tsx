import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { activeSets, SetChatBar, setChatQuestion } from "@/components/shots/SetChatBar";
import { renderWithQueryClient } from "@/test/renderWithQueryClient";
import { setRow } from "@/test/setsFixtures";

/** The Chat page's query string a bar button links to, as a map. */
function linkParams(link: HTMLElement): Record<string, string> {
  const href = link.getAttribute("href") ?? "";
  expect(href.startsWith("/chat?")).toBe(true);
  return Object.fromEntries(new URLSearchParams(href.slice("/chat?".length)));
}

describe("activeSets", () => {
  it("keeps the Sets being brewed, in the order the list serves them", () => {
    const sets = [
      setRow({ id: 5, name: "Guji" }),
      setRow({ id: 4, name: "Old bag", archived: true }),
      setRow({ id: 3, name: "Being designed", designing: true }),
      setRow({ id: 2, name: "No version", current_version_id: null, current_version_no: 0 }),
      setRow({ id: 1, name: "House blend" }),
    ];

    expect(activeSets(sets).map((row) => row.id)).toEqual([5, 1]);
  });
});

describe("setChatQuestion", () => {
  it("names the version and asks what the judged shots show", () => {
    expect(setChatQuestion("v1.2")).toBe(
      "I've judged my latest shots on v1.2. What do they show, and what should I change next?",
    );
  });
});

describe("SetChatBar", () => {
  it("links every active Set to its current version's conversation with the question typed", () => {
    renderWithQueryClient(
      <SetChatBar
        sets={[
          setRow({
            id: 5,
            name: "Guji on the Niche",
            current_version_id: 51,
            current_version_no: 4,
          }),
          setRow({ id: 4, name: "Kenya AA", archived: true }),
          setRow({ id: 2, name: "House blend", current_version_id: 21, current_version_no: 1 }),
        ]}
      />,
    );

    const bar = screen.getByRole("navigation", { name: "Chat about a Set" });
    const links = within(bar).getAllByRole("link");
    expect(links.map((link) => link.textContent)).toEqual([
      "Guji on the Niche · v4",
      "House blend · v1",
    ]);
    // The version is what makes the Chat page open or continue that version's
    // own conversation rather than only pointing at the Set.
    expect(linkParams(links[0])).toEqual({ set: "5", version: "51", ask: setChatQuestion("v4") });
    expect(linkParams(links[1])).toEqual({ set: "2", version: "21", ask: setChatQuestion("v1") });
  });

  it("names a minor version by its name, never by its ordinal", () => {
    renderWithQueryClient(
      <SetChatBar
        sets={[
          setRow({
            id: 5,
            name: "Guji on the Niche",
            current_version_id: 53,
            current_version_no: 3,
            current_version_label: "v1.2",
          }),
        ]}
      />,
    );

    const link = within(screen.getByRole("navigation", { name: "Chat about a Set" })).getByRole(
      "link",
    );
    expect(link).toHaveTextContent("Guji on the Niche · v1.2");
    expect(link).not.toHaveTextContent(/\bv3/);
    expect(linkParams(link).ask).toBe(setChatQuestion("v1.2"));
    expect(linkParams(link).ask).toContain("my latest shots on v1.2.");
  });

  it("draws nothing when no Set is active", () => {
    renderWithQueryClient(
      <SetChatBar sets={[setRow({ archived: true }), setRow({ id: 9, designing: true })]} />,
    );

    expect(screen.queryByTestId("set-chat-bar")).not.toBeInTheDocument();
  });
});
