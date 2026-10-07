import { BOXES_KEY, type BoxId, DEFAULT_BOXES } from "@/lib/shotBoxes";

/**
 * Opens (or folds) some of a shot's boxes the way a person's earlier choice would have: in storage,
 * before the page renders. The boxes are folded by default except the judgement, so a test about
 * what is inside one asks for it open here; a test about the defaults asks for nothing.
 */
export function openBoxes(open: Partial<Record<BoxId, boolean>>): void {
  window.localStorage.setItem(BOXES_KEY, JSON.stringify({ ...DEFAULT_BOXES, ...open }));
}

/** Every box that folds, open. */
export function openAllBoxes(): void {
  openBoxes({ judgement: true, curves: true, review: true, check: true });
}
