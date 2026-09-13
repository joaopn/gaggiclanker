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
 * So: no portal, no library. The panel is positioned by CSS against a wrapper,
 * or — in `anchored` mode, for a popover inside a scrolling list — `fixed`
 * against the anchor's own rectangle, measured once per open along with the
 * panel's own height so it can flip above the anchor and be clamped into the
 * viewport. Two `getBoundingClientRect` calls, no listeners following either.
 *
 * What it does keep is the behaviour people expect and radix would have given
 * us: focus moves into the panel on open and back to the trigger on every
 * close, Escape closes, a click outside closes, a scroll under an `anchored`
 * panel closes it rather than letting it hang over an unrelated row, the
 * trigger says `aria-expanded`, and the panel is a dialog that its caller
 * names.
 */

/** Breathing room between the panel and the edge of the viewport, in pixels. */
const VIEWPORT_MARGIN = 8;

/** Between the anchor and the panel, in pixels. */
const ANCHOR_GAP = 4;

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
 * absolutely positioned panel is clipped by the container that scrolls, so it
 * measures its anchor and itself and positions `fixed`. It prefers to hang
 * below the anchor, flips above when it does not fit, and is clamped into the
 * viewport either way — a panel whose Save button is below the fold cannot be
 * scrolled to, because the page's scroll does not move a fixed element and the
 * list's scroll does not either.
 *
 * It is measured once, on open, and nothing follows the anchor afterwards.
 * That is why an `anchored` panel closes as soon as anything scrolls: the row
 * it belongs to is about to move out from under it, and in a virtualised list
 * it is about to be unmounted entirely, taking the panel and anything typed
 * into it with it. Closing on the first pixel of scroll happens long before
 * the overscan runs out, so the panel goes away deliberately rather than
 * vanishing.
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
  const [fixedStyle, setFixedStyle] = useState<CSSProperties | null>(null);

  // Measured in a layout effect, so the panel is never painted at the wrong
  // place first — until it has a position it renders hidden (see `hidden`
  // below) rather than at the origin.
  useLayoutEffect(() => {
    if (!open || !anchored) {
      setFixedStyle(null);
      return;
    }
    const anchor = triggerRef.current?.getBoundingClientRect();
    const panel = contentRef.current?.getBoundingClientRect();
    if (!anchor || !panel) return;
    setFixedStyle(anchoredPosition(anchor, panel.height, align));
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
    }

    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open, setOpen, triggerRef]);

  // Any scroll anywhere closes an anchored panel — captured at the document,
  // because the thing that scrolls is a list several levels up and a scroll
  // event does not bubble. The panel's own overflow is excluded: a long
  // editor scrolling its own content must not close itself.
  useEffect(() => {
    if (!open || !anchored) return;
    function onScroll(event: Event) {
      const target = event.target as Node | null;
      if (target !== null && contentRef.current?.contains(target)) return;
      setOpen(false);
    }
    document.addEventListener("scroll", onScroll, true);
    return () => document.removeEventListener("scroll", onScroll, true);
  }, [open, anchored, setOpen]);

  // Focus goes in on open and back to the trigger on every close — Escape, a
  // click outside, and a Save or Cancel button that simply flips the state.
  // Landing on `<body>` is how a keyboard user loses their place. The guard is
  // that focus has not already gone somewhere deliberate: clicking a different
  // control *outside* the panel moves focus there and should keep it there,
  // while a button inside the panel is about to be removed from the document.
  useEffect(() => {
    if (!open) return;
    const panel = contentRef.current;
    const trigger = triggerRef.current;
    focusFirst(panel);
    return () => {
      const active = document.activeElement;
      if (active === null || active === document.body || panel?.contains(active)) {
        trigger?.focus();
      }
    };
  }, [open, triggerRef]);

  if (!open) return null;

  const positioned = !anchored || fixedStyle !== null;
  return (
    <div
      ref={contentRef}
      id={contentId}
      role="dialog"
      tabIndex={-1}
      style={
        anchored
          ? // Fixed from the first paint so the measurement reads the height it
            // will actually have, rather than one constrained by the list.
            { position: "fixed", top: 0, left: 0, visibility: "hidden", ...fixedStyle }
          : undefined
      }
      data-positioned={positioned ? "" : undefined}
      className={cn(
        "z-50 w-72 rounded-md border border-border bg-popover p-4 text-popover-foreground shadow-md",
        // A panel taller than the viewport scrolls itself. Without this the
        // clamp would put its top at the margin and its bottom off-screen.
        anchored && "max-h-[calc(100vh-1rem)] overflow-y-auto",
        !anchored && "absolute top-full mt-1",
        !anchored && (align === "end" ? "right-0" : "left-0"),
        className,
      )}
      {...props}
    >
      {children}
    </div>
  );
}

/**
 * Where a fixed panel of this height goes, given where its anchor is.
 *
 * Below the anchor when it fits, above it when it does not, and clamped into
 * the viewport when neither does — which is what happens to a panel taller
 * than the space above *and* below it, and is why it also scrolls itself.
 * Exported for its own test: the arithmetic is the whole of the behaviour and
 * jsdom reports every rectangle as zero.
 */
export function anchoredPosition(
  anchor: { top: number; bottom: number; left: number; right: number },
  panelHeight: number,
  align: "start" | "end",
  viewport: { width: number; height: number } = {
    width: window.innerWidth,
    height: window.innerHeight,
  },
): CSSProperties {
  const below = anchor.bottom + ANCHOR_GAP;
  const above = anchor.top - ANCHOR_GAP - panelHeight;
  const lowest = viewport.height - panelHeight - VIEWPORT_MARGIN;

  let top = below;
  if (below > lowest) {
    // Prefer flipping above the anchor; a clamped panel that still covers its
    // own row is better than one whose buttons are off the bottom of the
    // screen, which is what both of these are avoiding.
    top = above >= VIEWPORT_MARGIN ? above : Math.max(VIEWPORT_MARGIN, lowest);
  }

  return {
    position: "fixed",
    top,
    left: align === "end" ? undefined : Math.max(VIEWPORT_MARGIN, anchor.left),
    right: align === "end" ? Math.max(VIEWPORT_MARGIN, viewport.width - anchor.right) : undefined,
    visibility: "visible",
  };
}

/** The panel's first focusable child, or the panel. */
function focusFirst(panel: HTMLElement | null): void {
  if (panel === null) return;
  const focusable = panel.querySelector<HTMLElement>(
    'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
  );
  (focusable ?? panel).focus();
}
