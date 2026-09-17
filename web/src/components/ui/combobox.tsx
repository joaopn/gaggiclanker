import { type ComponentProps, type KeyboardEvent, useId, useMemo, useState } from "react";
import { cn } from "@/lib/utils";

/**
 * A text input that suggests values already in use, and accepts any other.
 *
 * The WAI-ARIA combobox with a listbox popup: the input keeps focus the whole
 * time and `aria-activedescendant` says which option the arrows are on, so a
 * screen reader follows the list without focus ever leaving the text.
 *
 * **Not radix, and not the popover primitive.** The suggestions are an absolutely
 * positioned list under the input inside a `relative` wrapper: nothing is
 * measured, so nothing costs floating-ui's fifteen seconds under jsdom, and a
 * list of eight short strings has no reason to escape its wrapper.
 *
 * Free text is the point, not a fallback: the value is whatever is in the
 * input, and a suggestion is only a shortcut to a spelling somebody used
 * before. That is also why Enter picks an option only when one is active — with
 * nothing highlighted, Enter belongs to the form and submits it as usual.
 */

/** Enough to choose from at a glance; the rest are a few more letters away. */
const MAX_OPTIONS = 8;

/**
 * The options worth showing for what is typed: a case-insensitive substring
 * match, without the one that is exactly what is already there (picking it
 * would change nothing).
 */
export function matchingOptions(
  options: readonly string[],
  typed: string,
  limit = MAX_OPTIONS,
): string[] {
  const needle = typed.trim().toLowerCase();
  return options
    .filter((option) => {
      const candidate = option.toLowerCase();
      return candidate !== needle && candidate.includes(needle);
    })
    .slice(0, limit);
}

type ComboboxProps = Omit<
  ComponentProps<"input">,
  "value" | "onChange" | "role" | "type" | "list" | "children"
> & {
  value: string;
  onValueChange: (value: string) => void;
  options: readonly string[];
  /** The listbox's accessible name, e.g. "Roasters already recorded". */
  listLabel: string;
  /** Classes for the input itself; the wrapper is always `relative`. */
  className?: string;
};

export function Combobox({
  value,
  onValueChange,
  options,
  listLabel,
  className,
  onKeyDown,
  onBlur,
  ...inputProps
}: ComboboxProps) {
  const listId = useId();
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const matches = useMemo(() => matchingOptions(options, value), [options, value]);
  const expanded = open && matches.length > 0;
  // Clamped at read time: a keystroke can shrink the list under the active index.
  const activeIndex = expanded && active < matches.length ? active : -1;
  const optionId = (index: number) => `${listId}-option-${index}`;

  function close() {
    setOpen(false);
    setActive(-1);
  }

  function pick(option: string) {
    onValueChange(option);
    close();
  }

  function handleKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    onKeyDown?.(event);
    if (event.defaultPrevented || event.nativeEvent.isComposing) return;
    switch (event.key) {
      case "ArrowDown": {
        event.preventDefault();
        if (!expanded) {
          setOpen(true);
          setActive(0);
        } else {
          setActive((activeIndex + 1) % matches.length);
        }
        break;
      }
      case "ArrowUp": {
        if (!expanded) return;
        event.preventDefault();
        setActive(activeIndex <= 0 ? matches.length - 1 : activeIndex - 1);
        break;
      }
      case "Enter": {
        // Only an active option claims the key; otherwise the form submits.
        if (activeIndex < 0) return;
        event.preventDefault();
        pick(matches[activeIndex]);
        break;
      }
      case "Escape": {
        if (!expanded) return;
        // Closing the list is all this Escape means: a dialog or a panel
        // around the form must not close with it.
        event.preventDefault();
        event.stopPropagation();
        close();
        break;
      }
      case "Tab": {
        close();
        break;
      }
    }
  }

  return (
    <div className="relative">
      <input
        {...inputProps}
        type="text"
        role="combobox"
        autoComplete="off"
        aria-autocomplete="list"
        aria-expanded={expanded}
        aria-controls={listId}
        aria-activedescendant={activeIndex >= 0 ? optionId(activeIndex) : undefined}
        className={className}
        value={value}
        onChange={(event) => {
          onValueChange(event.target.value);
          setOpen(true);
          setActive(-1);
        }}
        onKeyDown={handleKeyDown}
        onBlur={(event) => {
          close();
          onBlur?.(event);
        }}
      />
      {/* Always rendered, toggled with `hidden`: `aria-controls` has to point
          at an element that exists. */}
      <div
        id={listId}
        role="listbox"
        aria-label={listLabel}
        hidden={!expanded}
        className={cn(
          "absolute top-full right-0 left-0 z-20 mt-1 max-h-60 overflow-auto",
          "rounded-md border border-border bg-popover p-1 text-popover-foreground text-sm shadow-md",
        )}
      >
        {expanded
          ? matches.map((option, index) => (
              <div
                key={option}
                id={optionId(index)}
                role="option"
                tabIndex={-1}
                aria-selected={index === activeIndex}
                className={cn(
                  "cursor-pointer rounded-sm px-2 py-1",
                  index === activeIndex ? "bg-accent text-accent-foreground" : "hover:bg-muted",
                )}
                // mousedown rather than click: a click lands after the input's
                // blur, which has already closed the list the option was in.
                onMouseDown={(event) => {
                  event.preventDefault();
                  pick(option);
                }}
                onMouseEnter={() => setActive(index)}
              >
                {option}
              </div>
            ))
          : null}
      </div>
    </div>
  );
}
