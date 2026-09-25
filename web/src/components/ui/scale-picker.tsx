import { cn } from "@/lib/utils";

const STEPS = [1, 2, 3, 4, 5] as const;

/**
 * A 1-to-5 reading, picked by clicking a step: a bean's acidity, intensity or
 * sweetness.
 *
 * Numbered steps rather than stars, because this is not a verdict: acidity 1
 * is not a worse coffee than acidity 5. The steps up to the value are filled,
 * so it reads as a level at a glance.
 *
 * Like the rating stars, a row of buttons rather than one control with five
 * states, so each value is reachable with a tab and a space bar, and clicking
 * the value already chosen clears it: "not stated" is an answer, and without
 * a way back a mis-click would be permanent. A fieldset, so its legend names
 * the five buttons as one field.
 */
export function ScalePicker({
  label,
  name,
  value,
  onChange,
  low = "Low",
  high = "High",
}: {
  /** The field's visible name ("Acidity"). */
  label: string;
  /** What is being measured, for each step's spoken label ("acidity"). */
  name: string;
  value: number | null | undefined;
  /** `null` means the value was cleared. */
  onChange: (value: number | null) => void;
  low?: string;
  high?: string;
}) {
  const current = value ?? null;
  return (
    <fieldset className="m-0 min-w-0 border-0 p-0" data-testid={`scale-${name}`}>
      <legend className="mb-1 block p-0 text-muted-foreground text-xs">{label}</legend>
      <div className="flex h-8 items-center gap-1.5" data-value={current ?? undefined}>
        <span className="text-muted-foreground text-xs" aria-hidden="true">
          {low}
        </span>
        {STEPS.map((step) => (
          <button
            key={step}
            type="button"
            onClick={() => onChange(step === current ? null : step)}
            aria-pressed={step === current}
            aria-label={
              step === current
                ? `Clear the ${name}`
                : `${capitalise(name)} ${step} of 5${current ? `, currently ${current}` : ""}`
            }
            className={cn(
              "size-6 rounded-sm border text-xs tabular-nums",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
              current !== null && step <= current
                ? "border-primary bg-primary text-primary-foreground"
                : "border-input bg-background text-muted-foreground hover:bg-muted",
            )}
          >
            {step}
          </button>
        ))}
        <span className="text-muted-foreground text-xs" aria-hidden="true">
          {high}
        </span>
        {current === null ? <span className="sr-only">not stated</span> : null}
      </div>
    </fieldset>
  );
}

function capitalise(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1);
}
