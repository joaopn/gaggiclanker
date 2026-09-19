import { describe, expect, it } from "vitest";
import {
  CATEGORY_COLORS,
  flattenWheel,
  inWheelOrder,
  pathLabel,
  segmentColor,
  sunburst,
  textOn,
  toggleNote,
} from "@/lib/flavorWheel";
import { vocabulary } from "@/test/setsFixtures";

const wheel = vocabulary.flavor_wheel;
const flat = flattenWheel(wheel);

describe("flattenWheel", () => {
  it("puts every node before what sits outside it, with its path and category", () => {
    expect(flat.slice(0, 4).map((note) => note.value)).toEqual([
      "floral",
      "floral.black_tea",
      "floral.floral",
      "floral.floral.jasmine",
    ]);
    const blackberry = flat.find((note) => note.value === "fruity.berry.blackberry");
    expect(blackberry).toMatchObject({ depth: 2, category: "fruity" });
    expect(pathLabel(blackberry as (typeof flat)[number])).toBe("Fruity › Berry › Blackberry");
  });
});

describe("wheel order", () => {
  it("sorts and de-duplicates, and keeps an unknown note at the end", () => {
    expect(inWheelOrder(["sweet", "gone", "floral", "sweet"], flat)).toEqual([
      "floral",
      "sweet",
      "gone",
    ]);
  });

  it("toggles a note in its place and out again", () => {
    const added = toggleNote(["floral", "sweet"], "fruity.berry", flat);
    expect(added).toEqual(["floral", "fruity.berry", "sweet"]);
    expect(toggleNote(added, "fruity.berry", flat)).toEqual(["floral", "sweet"]);
  });
});

describe("sunburst", () => {
  const segments = sunburst(wheel);
  const of = (value: string) => segments.find((segment) => segment.note.value === value);

  it("draws one segment per node and closes the circle", () => {
    expect(segments).toHaveLength(flat.length);
    const categories = segments.filter((segment) => segment.innerRing === 0);
    expect(categories[0].start).toBe(0);
    expect(categories.at(-1)?.end).toBeCloseTo(360);
  });

  it("sizes each node by the notes it holds at the rim", () => {
    // Floral holds Black tea and Jasmine: two of the fixture's eleven rim notes.
    const floral = of("floral");
    expect((floral?.end ?? 0) - (floral?.start ?? 0)).toBeCloseTo((2 / 11) * 360);
  });

  it("lets a group with nothing outside it span both outer rings", () => {
    expect(of("floral.black_tea")).toMatchObject({ innerRing: 1, outerRing: 2 });
    expect(of("floral.floral")).toMatchObject({ innerRing: 1, outerRing: 1 });
    expect(of("floral.floral.jasmine")).toMatchObject({ innerRing: 2, outerRing: 2 });
  });
});

describe("colours", () => {
  it("uses the category's colour at the centre and lighter shades outwards", () => {
    expect(segmentColor("fruity", 0)).toBe(CATEGORY_COLORS.fruity);
    expect(segmentColor("fruity", 1)).not.toBe(segmentColor("fruity", 2));
  });

  it("picks text that reads on each fill", () => {
    expect(textOn("#187A2F")).toBe("#ffffff");
    expect(textOn("#EBB40F")).toBe("#1c1917");
  });
});
