import type { FlavorNode } from "@/api/types";

/**
 * Reading the flavour wheel `/api/vocab` serves.
 *
 * The wheel itself — every note, its label, where it sits — comes from the
 * server; nothing here types a coffee word. What is here is how to walk it and
 * how to draw it: the flat list in wheel order, a note's path from the centre,
 * and the geometry of the sunburst. The category colours are the one thing
 * that lives only on the client, because they are presentation.
 */

/** One note of the wheel, flattened, with what the drawing and the lists need. */
export type WheelNote = {
  value: string;
  label: string;
  /** 0 for a category, 1 for a group, 2 for a note at the rim. */
  depth: number;
  /** The category's slug, which picks the colour. */
  category: string;
  /** Labels from the centre out, this note's last. */
  path: string[];
};

/** Every node, each before what sits outside it: the order the wheel is read in. */
export function flattenWheel(wheel: readonly FlavorNode[]): WheelNote[] {
  const out: WheelNote[] = [];
  const walk = (nodes: readonly FlavorNode[], parents: string[], category: string | null) => {
    for (const node of nodes) {
      const path = [...parents, node.label];
      out.push({
        value: node.value,
        label: node.label,
        depth: parents.length,
        category: category ?? node.value,
        path,
      });
      walk(node.children ?? [], path, category ?? node.value);
    }
  };
  walk(wheel, [], null);
  return out;
}

/** How a person reads a note: "Fruity › Berry › Blackberry". */
export function pathLabel(note: Pick<WheelNote, "path">): string {
  return note.path.join(" › ");
}

/**
 * Notes in wheel order, de-duplicated. A note the wheel does not know goes
 * last rather than vanishing: it is still recorded on a shot and somebody may
 * want to remove it.
 */
export function inWheelOrder(notes: readonly string[], wheel: readonly WheelNote[]): string[] {
  const rank = new Map(wheel.map((note, index) => [note.value, index]));
  return [...new Set(notes)].sort(
    (a, b) => (rank.get(a) ?? wheel.length) - (rank.get(b) ?? wheel.length),
  );
}

/** A note added to a list, or taken off it, keeping wheel order. */
export function toggleNote(
  notes: readonly string[],
  note: string,
  wheel: readonly WheelNote[],
): string[] {
  return notes.includes(note)
    ? notes.filter((value) => value !== note)
    : inWheelOrder([...notes, note], wheel);
}

/**
 * The printed wheel's category colours, close enough to recognise it by. Keyed
 * by category slug; a category the server adds later falls back to grey
 * rather than failing.
 */
export const CATEGORY_COLORS: Record<string, string> = {
  floral: "#DA1D81",
  fruity: "#DA1D23",
  sour_fermented: "#EBB40F",
  green_vegetative: "#187A2F",
  other: "#0AA3B5",
  roasted: "#C94930",
  spices: "#AD213E",
  nutty_cocoa: "#A87B64",
  sweet: "#E65832",
};

const FALLBACK_COLOR = "#8A8A8A";

function parseHex(hex: string): [number, number, number] {
  const value = Number.parseInt(hex.slice(1), 16);
  return [(value >> 16) & 255, (value >> 8) & 255, value & 255];
}

function toHex([r, g, b]: [number, number, number]): string {
  return `#${[r, g, b].map((part) => Math.round(part).toString(16).padStart(2, "0")).join("")}`;
}

/** `hex` moved `amount` (0..1) of the way to white. */
function lighten(hex: string, amount: number): string {
  const [r, g, b] = parseHex(hex);
  return toHex([r + (255 - r) * amount, g + (255 - g) * amount, b + (255 - b) * amount]);
}

/**
 * A segment's fill: the category's colour at the centre, lighter towards the
 * rim, as on the printed wheel. The fills do not change with the theme — they
 * are the wheel's own colours — so the text on them is picked per fill
 * (`textOn`) rather than taken from the theme.
 */
export function segmentColor(category: string, depth: number): string {
  const base = CATEGORY_COLORS[category] ?? FALLBACK_COLOR;
  return depth === 0 ? base : lighten(base, depth === 1 ? 0.22 : 0.42);
}

/** Relative luminance, WCAG's definition. */
function luminance(hex: string): number {
  const [r, g, b] = parseHex(hex).map((part) => {
    const c = part / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

/** Near-black or white, whichever reads better on `fill`. */
export function textOn(fill: string): string {
  const dark = "#1c1917";
  const contrast = (a: number, b: number) => (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
  const l = luminance(fill);
  return contrast(l, luminance(dark)) >= contrast(l, 1) ? dark : "#ffffff";
}

/** One drawn segment of the sunburst. Angles in degrees, clockwise from the top. */
export type Segment = {
  note: WheelNote;
  start: number;
  end: number;
  /** Which rings it covers, 0 (centre) to 2 (rim), inclusive. */
  innerRing: number;
  outerRing: number;
};

/**
 * The sunburst's segments. Every node's angle is proportional to how many
 * notes it holds at the rim (one, for a node with nothing outside it), so the
 * rim is evenly spaced and each category's slice is as wide as its notes need.
 * A group with no notes outside it spans the two outer rings rather than
 * leaving a hole where its notes would be.
 */
export function sunburst(wheel: readonly FlavorNode[]): Segment[] {
  const flat = flattenWheel(wheel);
  const byValue = new Map(flat.map((note) => [note.value, note]));
  const leaves = (node: FlavorNode): number =>
    node.children?.length ? node.children.reduce((sum, child) => sum + leaves(child), 0) : 1;
  const total = wheel.reduce((sum, node) => sum + leaves(node), 0);
  const segments: Segment[] = [];
  const place = (nodes: readonly FlavorNode[], from: number, depth: number) => {
    let at = from;
    for (const node of nodes) {
      const span = (leaves(node) / total) * 360;
      const note = byValue.get(node.value) as WheelNote;
      const hasChildren = Boolean(node.children?.length);
      segments.push({
        note,
        start: at,
        end: at + span,
        innerRing: depth,
        outerRing: hasChildren ? depth : 2,
      });
      if (hasChildren) place(node.children ?? [], at, depth + 1);
      at += span;
    }
  };
  if (total > 0) place(wheel, 0, 0);
  return segments;
}
