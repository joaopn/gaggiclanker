import { useEffect, useMemo, useRef } from "react";
import { tinykeys } from "tinykeys";

type KeyBindingMap = Record<string, (event: KeyboardEvent) => void>;

const INPUT_TAG_NAMES = new Set(["INPUT", "TEXTAREA", "SELECT"]);
const NON_SHIFT_MODIFIER_PATTERN = /(?:^|\+)(\$mod|Control|Meta|Alt)(?:\+|$)/;

function isEditableTarget(event: KeyboardEvent): boolean {
  const target = event.target;
  if (!(target instanceof HTMLElement)) return false;
  if (INPUT_TAG_NAMES.has(target.tagName)) return true;
  return target.isContentEditable;
}

/**
 * A thin React wrapper around `tinykeys`.
 *
 * - Unsubscribes on unmount.
 * - Skips single-key and shift-only bindings while focus is in a text field,
 *   so typing "gs" into the settings form does not navigate away mid-sentence.
 * - Holds handlers in a ref and rebinds only when the *set of keys* changes, so
 *   inline arrow functions are free.
 */
export function useHotkeys(bindings: KeyBindingMap, options: { enabled?: boolean } = {}): void {
  const { enabled = true } = options;
  const bindingsRef = useRef(bindings);
  bindingsRef.current = bindings;
  const signature = useMemo(() => Object.keys(bindings).sort().join("|"), [bindings]);

  useEffect(() => {
    if (!enabled) return;
    const guarded: KeyBindingMap = {};
    for (const key of signature ? signature.split("|") : []) {
      // A non-shift modifier means the user reached for a deliberate chord, so
      // it fires even in an input. Shift alone is just punctuation.
      const hasModifier = key
        .split(" ")
        .some((sequence) => NON_SHIFT_MODIFIER_PATTERN.test(sequence));
      guarded[key] = (event: KeyboardEvent) => {
        if (!hasModifier && isEditableTarget(event)) return;
        bindingsRef.current[key]?.(event);
      };
    }
    return tinykeys(window, guarded);
  }, [enabled, signature]);
}
