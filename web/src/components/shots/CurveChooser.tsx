import { SlidersHorizontal } from "lucide-react";
import { useId, useRef, useState } from "react";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { SHOT_SERIES } from "@/lib/shotChart";
import {
  CURVE_COLOURS,
  type CurveChoice,
  curveColour,
  DEFAULT_CURVES,
  defaultCurveColour,
} from "@/lib/shotCurves";
import { cn } from "@/lib/utils";

/**
 * Which curves the Curve column draws, and in what colour.
 *
 * Sits in the column's heading: the choice is made while looking at the
 * column it changes. The panel is `anchored` (fixed against the button) because
 * the heading is the top of a scrolling box that would clip an absolute panel,
 * and it is clamped into the viewport, which matters at phone width.
 *
 * The last shown curve cannot be hidden, so its checkbox is disabled rather
 * than silently ignored. A hidden curve's swatch stays usable (dimmed): its
 * colour is kept while it is hidden, so it can be chosen before the curve is
 * shown and is there again when it is.
 */
export function CurveChooser({
  choice,
  onChange,
}: {
  choice: CurveChoice;
  onChange: (next: CurveChoice) => void;
}) {
  const [open, setOpen] = useState(false);
  const [picking, setPicking] = useState<string | null>(null);
  const prefix = useId();
  const swatches = useRef<Record<string, HTMLButtonElement | null>>({});
  const listRef = useRef<HTMLFieldSetElement>(null);
  const shownCount = choice.shown.length;

  function toggle(key: string) {
    if (choice.shown.includes(key)) {
      if (shownCount === 1) return;
      onChange({ ...choice, shown: choice.shown.filter((value) => value !== key) });
    } else {
      onChange({ ...choice, shown: [...choice.shown, key] });
    }
  }

  function colour(key: string, token: string) {
    onChange({ ...choice, colors: { ...choice.colors, [key]: token } });
    setPicking(null);
    // The palette is about to be hidden with the focused button in it, and
    // focus on a hidden element falls to `<body>`, outside the panel: the next
    // Tab would leave the menu. The row's swatch is where the person was.
    swatches.current[key]?.focus({ preventScroll: true });
  }

  function reset() {
    setPicking(null);
    onChange(DEFAULT_CURVES);
    // Reset disables itself once it has done its work, which drops focus the
    // same way; the first checkbox is the top of the menu.
    listRef.current?.querySelector<HTMLElement>("input")?.focus({ preventScroll: true });
  }

  // Anywhere in the panel outside the open palette and its own swatch closes
  // the palette: it lies over the next row's swatch, and a palette that only
  // its own swatch could close would hide that one.
  function closePaletteOnOutsidePress(event: React.MouseEvent) {
    if (picking === null) return;
    const target = event.target as Element;
    if (target.closest("[data-palette]:not([hidden])")) return;
    if (target.closest(`[data-swatch="${picking}"]`)) return;
    setPicking(null);
  }

  return (
    <Popover
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (!next) setPicking(null);
      }}
    >
      <PopoverTrigger asChild>
        <button
          type="button"
          aria-label="Choose curves"
          data-testid="curves-button"
          className="inline-flex shrink-0 rounded p-0.5 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <SlidersHorizontal className="size-3" aria-hidden="true" />
        </button>
      </PopoverTrigger>
      <PopoverContent
        anchored
        // Bounded to the viewport and scrolling itself: the nine rows and an open
        // palette must never leave the Reset button below the fold.
        className="max-h-[calc(100vh-1rem)] w-64 overflow-y-auto text-foreground normal-case tracking-normal"
        data-testid="curves-menu"
        onMouseDown={closePaletteOnOutsidePress}
        aria-label="Curves"
      >
        <fieldset ref={listRef} className="space-y-1.5 text-left">
          <legend className="mb-2 text-muted-foreground text-xs">Curves</legend>
          {SHOT_SERIES.map((spec) => {
            const id = `${prefix}-${spec.key}`;
            const token = curveColour(choice, spec.key);
            const shown = choice.shown.includes(spec.key);
            return (
              <div key={spec.key} data-series={spec.key} className="relative">
                <div className="flex items-center gap-2">
                  <input
                    id={id}
                    type="checkbox"
                    className="size-3.5 accent-primary"
                    checked={shown}
                    disabled={shown && shownCount === 1}
                    onChange={() => toggle(spec.key)}
                  />
                  <label htmlFor={id} className="flex-1 text-sm">
                    {spec.label}
                  </label>
                  <button
                    type="button"
                    aria-label={`Colour of ${spec.label}`}
                    aria-expanded={picking === spec.key}
                    aria-controls={`${id}-colours`}
                    data-swatch={spec.key}
                    ref={(node) => {
                      swatches.current[spec.key] = node;
                    }}
                    onClick={() => setPicking(picking === spec.key ? null : spec.key)}
                    className={cn(
                      "size-4 rounded-full border border-border",
                      !shown && "opacity-50",
                    )}
                    style={{ background: `var(${token})` }}
                  />
                </div>
                {/* Always rendered and toggled with `hidden`, so `aria-controls`
                    has something to point at. Laid over the rows below rather
                    than pushing them: the panel was measured when it opened,
                    and a palette that made it taller would push the last row's
                    palette (and the Reset button) off the bottom of the screen. */}
                <fieldset
                  id={`${id}-colours`}
                  data-palette=""
                  hidden={picking !== spec.key}
                  onKeyDown={(event) => {
                    // Escape in a palette closes the palette, not the menu: the
                    // popover's own listener sits on the document and never
                    // sees a key stopped here.
                    if (event.key !== "Escape") return;
                    event.stopPropagation();
                    setPicking(null);
                    swatches.current[spec.key]?.focus({ preventScroll: true });
                  }}
                  className="absolute top-full right-0 z-10 mt-1 min-w-0 rounded-md border border-border bg-popover p-1.5 shadow-md"
                >
                  <legend className="sr-only">Colours for {spec.label}</legend>
                  <div className="flex gap-1.5">
                    {CURVE_COLOURS.map((option) => (
                      <button
                        key={option.token}
                        type="button"
                        aria-label={`${option.label} for ${spec.label}`}
                        aria-pressed={token === option.token}
                        onClick={() => colour(spec.key, option.token)}
                        className={cn(
                          "size-4 rounded-full border border-border",
                          token === option.token && "ring-2 ring-ring ring-offset-1",
                        )}
                        style={{ background: `var(${option.token})` }}
                      />
                    ))}
                  </div>
                </fieldset>
              </div>
            );
          })}
        </fieldset>
        <button
          type="button"
          data-testid="reset-curves"
          className="mt-3 w-full rounded-md px-2 py-1.5 text-sm hover:bg-accent disabled:opacity-50 disabled:hover:bg-transparent"
          disabled={isDefault(choice)}
          onClick={reset}
        >
          Reset to default
        </button>
      </PopoverContent>
    </Popover>
  );
}

/**
 * Whether the choice is the default, so that Reset has nothing to do. A colour
 * picked and equal to the series' own default is the default too: Reset would
 * change nothing on screen.
 */
function isDefault(choice: CurveChoice): boolean {
  return (
    Object.entries(choice.colors).every(([key, token]) => token === defaultCurveColour(key)) &&
    choice.shown.length === DEFAULT_CURVES.shown.length &&
    DEFAULT_CURVES.shown.every((key) => choice.shown.includes(key))
  );
}
