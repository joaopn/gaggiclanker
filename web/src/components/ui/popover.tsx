import {
  type CSSProperties,
  cloneElement,
  createContext,
  isValidElement,
  type ReactElement,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { cn } from "@/lib/utils";

/**
 * A panel anchored to the thing that opened it.
 *
 * **Not radix, unlike every other primitive in this directory, and that is a
 * deliberate exception.** Radix's overlays position themselves with
 * floating-ui, and floating-ui in jsdom is pathologically slow: one
 * `computePosition` with the middleware radix passes costs about fifteen
 * seconds here, because every collision check walks the tree calling
 * `getComputedStyle`. There is no headless browser in this project, so a
 * primitive whose content cannot be opened in a test is a primitive whose
 * behaviour is never checked — and the two places this is used, the filter
 * panel and the row editor, are exactly where the behaviour matters.
 *
 * So: no collision detection, no flipping, no portal. The panel is positioned
 * by CSS against a wrapper, or `fixed` against the anchor's own rectangle when
 * it has to escape a scrolling container. That is enough for a menu that hangs
 * off a button, and it costs one `getBoundingClientRect` per open.
 *
 * What it does keep is the behaviour people expect and radix would have given
 * us: Escape closes and returns focus to the trigger, a click outside closes,
 * the trigger says `aria-expanded`, and the panel is a labelled dialog.
 */

type PopoverContextValue = {
  open: boolean;
  setOpen: (open: boolean) => void;
  contentId: string;
  triggerRef: React.MutableRefObject<HTMLElement | null>;
};

const PopoverContext = createContext<PopoverContextValue | null>(null);

function usePopover(component: string): PopoverContextValue {
  const context = useContext(PopoverContext);
  if (context === null) {
    throw new Error(`<${component}> must be used inside a <Popover>`);
  }
  return context;
}

export function Popover({
  open: controlledOpen,
  onOpenChange,
  defaultOpen = false,
  children,
}: {
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  defaultOpen?: boolean;
  children: ReactNode;
}) {
  const [uncontrolledOpen, setUncontrolledOpen] = useState(defaultOpen);
  const triggerRef = useRef<HTMLElement | null>(null);
  const contentId = useId();
  const open = controlledOpen ?? uncontrolledOpen;

  const setOpen = useCallback(
    (next: boolean) => {
      if (controlledOpen === undefined) setUncontrolledOpen(next);
      onOpenChange?.(next);
    },
    [controlledOpen, onOpenChange],
  );

  const value = useMemo(
    () => ({ open, setOpen, contentId, triggerRef }),
    [open, setOpen, contentId],
  );

  return (
    <PopoverContext.Provider value={value}>
      <div className="relative inline-block">{children}</div>
    </PopoverContext.Provider>
  );
}

type TriggerChildProps = {
  ref?: (node: HTMLElement | null) => void;
  onClick?: (event: React.MouseEvent<HTMLElement>) => void;
  "aria-expanded"?: boolean;
  "aria-controls"?: string;
  "aria-haspopup"?: "dialog";
};

/**
 * Whatever opens the panel.
 *
 * `asChild` follows the shadcn convention so a `Button` can be the trigger and
 * keep its own styling. The child is cloned rather than wrapped, because a
 * button inside a button is invalid HTML and a wrapper div would break the
 * header's grid.
 */
export function PopoverTrigger({
  asChild = false,
  children,
  ...props
}: {
  asChild?: boolean;
  children: ReactNode;
} & React.ButtonHTMLAttributes<HTMLButtonElement>) {
  const { open, setOpen, contentId, triggerRef } = usePopover("PopoverTrigger");

  const shared: TriggerChildProps = {
    ref: (node: HTMLElement | null) => {
      triggerRef.current = node;
    },
    "aria-expanded": open,
    "aria-controls": open ? contentId : undefined,
    "aria-haspopup": "dialog",
  };

  if (asChild && isValidElement(children)) {
    const child = children as ReactElement<TriggerChildProps>;
    return cloneElement(child, {
      ...shared,
      onClick: (event: React.MouseEvent<HTMLElement>) => {
        child.props.onClick?.(event);
        if (!event.defaultPrevented) setOpen(!open);
      },
    });
  }

  return (
    <button type="button" {...props} {...shared} onClick={() => setOpen(!open)}>
      {children}
    </button>
  );
}

/**
 * The panel.
 *
 * `anchored` is the escape hatch for a popover inside a scrolling list: an
 * absolutely positioned panel is clipped by the container that scrolls, so the
 * row editor measures its anchor once and positions itself `fixed`. Once, on
 * open — this does not follow the row if the list scrolls underneath it, and
 * it does not need to: the panel closes on the first click outside it.
 */
export function PopoverContent({
  className,
  align = "start",
  anchored = false,
  children,
  ...props
}: {
  align?: "start" | "end";
  /** Position against the trigger's rectangle instead of against the wrapper. */
  anchored?: boolean;
  children: ReactNode;
} & React.HTMLAttributes<HTMLDivElement>) {
  const { open, setOpen, contentId, triggerRef } = usePopover("PopoverContent");
  const contentRef = useRef<HTMLDivElement>(null);
  const [fixedStyle, setFixedStyle] = useState<CSSProperties | undefined>(undefined);

  // Measured in a layout effect so the panel is never painted at the origin
  // first. One rectangle, one read; there is no resize or scroll listener
  // behind it on purpose.
  useLayoutEffect(() => {
    if (!open || !anchored) {
      setFixedStyle(undefined);
      return;
    }
    const rect = triggerRef.current?.getBoundingClientRect();
    if (!rect) return;
    setFixedStyle({
      position: "fixed",
      top: rect.bottom + 4,
      left: align === "end" ? undefined : rect.left,
      right: align === "end" ? Math.max(8, window.innerWidth - rect.right) : undefined,
    });
  }, [open, anchored, align, triggerRef]);

  useEffect(() => {
    if (!open) return;

    function onPointerDown(event: MouseEvent) {
      const target = event.target as Node | null;
      if (target === null) return;
      if (contentRef.current?.contains(target)) return;
      if (triggerRef.current?.contains(target)) return;
      setOpen(false);
    }

    function onKeyDown(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      setOpen(false);
      // Focus goes back where it came from: closing a panel with the keyboard
      // and landing on `<body>` is how a keyboard user loses their place.
      triggerRef.current?.focus();
    }

    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open, setOpen, triggerRef]);

  if (!open) return null;

  return (
    <div
      ref={contentRef}
      id={contentId}
      role="dialog"
      style={fixedStyle}
      className={cn(
        "z-50 w-72 rounded-md border border-border bg-popover p-4 text-popover-foreground shadow-md",
        fixedStyle === undefined && "absolute top-full mt-1",
        fixedStyle === undefined && (align === "end" ? "right-0" : "left-0"),
        className,
      )}
      {...props}
    >
      {children}
    </div>
  );
}
