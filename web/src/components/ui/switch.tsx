import { cn } from "@/lib/utils";

/**
 * An on/off switch: a button with `role="switch"`, so a label names it and a test reads its
 * state from `aria-checked`. Hand-written like the popover: nothing portalled, nothing to
 * open.
 */
export function Switch({
  checked,
  onCheckedChange,
  disabled = false,
  label,
  className,
  ...rest
}: {
  checked: boolean;
  onCheckedChange: (next: boolean) => void;
  disabled?: boolean;
  /** The accessible name; the visible text sits beside the switch. */
  label: string;
  className?: string;
} & Omit<React.ComponentProps<"button">, "onChange" | "role" | "aria-checked" | "aria-label">) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onCheckedChange(!checked)}
      className={cn(
        "relative inline-flex h-5 w-9 shrink-0 items-center rounded-full border border-transparent transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50",
        checked ? "bg-primary" : "bg-input",
        className,
      )}
      {...rest}
    >
      <span
        aria-hidden="true"
        className={cn(
          "pointer-events-none block size-4 rounded-full bg-background shadow transition-transform",
          checked ? "translate-x-4" : "translate-x-0.5",
        )}
      />
    </button>
  );
}
