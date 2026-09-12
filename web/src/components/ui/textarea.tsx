import type * as React from "react";
import { cn } from "@/lib/utils";

/**
 * The shadcn textarea, added for the prompt editor.
 *
 * `field-sizing-content` is deliberately NOT used here: a prompt is hundreds of
 * lines and a box that grows to fit one would push the save button off the
 * screen. The editor sets its own height and scrolls.
 */
function Textarea({ className, ...props }: React.ComponentProps<"textarea">) {
  return (
    <textarea
      data-slot="textarea"
      className={cn(
        "flex w-full rounded-md border border-input bg-transparent px-3 py-2 text-base shadow-xs transition-[color,box-shadow] outline-none placeholder:text-muted-foreground disabled:cursor-not-allowed disabled:opacity-50 md:text-sm dark:bg-input/30",
        "focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50",
        "aria-invalid:border-destructive aria-invalid:ring-destructive/20 dark:aria-invalid:ring-destructive/40",
        className,
      )}
      {...props}
    />
  );
}

export { Textarea };
