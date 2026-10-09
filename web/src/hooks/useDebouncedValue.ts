import { useEffect, useState } from "react";

/**
 * A value that follows `value` after it has stopped changing for `delayMs`: for a request made
 * as somebody types, so a keystroke is not a request. The first value is returned at once.
 */
export function useDebouncedValue<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    if (Object.is(debounced, value)) return;
    const timer = setTimeout(() => setDebounced(value), delayMs);
    return () => clearTimeout(timer);
  }, [value, delayMs, debounced]);
  return debounced;
}
