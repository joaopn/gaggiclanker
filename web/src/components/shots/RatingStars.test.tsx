import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { RatingStars } from "@/components/shots/RatingStars";

/** The classes of the lit stars, by the served band; the web never works the band out itself. */
function litStars(): string[] {
  return Array.from(screen.getByTestId("rating-stars").querySelectorAll("svg"))
    .filter((star) => star.getAttribute("class")?.includes("fill-"))
    .map((star) => star.getAttribute("class") ?? "");
}

describe("RatingStars", () => {
  it.each([
    ["good", 5, "fill-status-good"],
    ["neutral", 3, "fill-muted-foreground"],
    ["bad", 2, "fill-status-warn"],
  ] as const)("lights %s stars in the band's colour", (band, rating, colour) => {
    render(<RatingStars rating={rating} band={band} />);

    const lit = litStars();
    expect(lit).toHaveLength(rating);
    for (const star of lit) expect(star).toContain(colour);
    expect(screen.getByTestId("rating-stars")).toHaveAttribute("data-band", band);
  });

  it("colours the clickable stars the same way, and leaves the unlit ones faint", () => {
    render(<RatingStars rating={4} band="good" ownRating={null} onRate={() => {}} />);

    const stars = Array.from(screen.getByTestId("rating-stars").querySelectorAll("svg"));
    expect(stars.map((star) => star.getAttribute("class")?.includes("fill-status-good"))).toEqual([
      true,
      true,
      true,
      true,
      false,
    ]);
    expect(stars[4].getAttribute("class")).toContain("text-muted-foreground/40");
  });

  it("keeps the warning colour for a rating with no served band", () => {
    render(<RatingStars rating={3} />);

    for (const star of litStars()) expect(star).toContain("fill-status-warn");
  });

  it("says not rated, and colours nothing, for no rating", () => {
    render(<RatingStars rating={null} band={null} />);

    expect(screen.getByRole("img", { name: "not rated" })).toBeInTheDocument();
  });
});
