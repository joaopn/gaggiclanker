import { type ReactNode, useEffect, useRef } from "react";
import { Button } from "@/components/ui/button";

/**
 * The inline "are you sure" under an action that cannot be undone.
 *
 * Inline rather than a dialog: the question sits directly under the list it is
 * about, so what is about to change stays on screen while somebody
 * decides, and nothing positioned or portalled has to open for a test to drive
 * it. Focus is left where it is on purpose — putting it on the confirm button
 * would make a stray Enter the decision.
 */
export function ConfirmStrip({
  title,
  children,
  confirmLabel,
  onConfirm,
  onCancel,
  testId,
  confirmVariant = "destructive",
  focusOnOpen = false,
}: {
  title: string;
  children: ReactNode;
  confirmLabel: string;
  onConfirm: () => void;
  onCancel: () => void;
  testId: string;
  /** `destructive` for something that cannot be undone; `default` for a step that can. */
  confirmVariant?: "destructive" | "default";
  /**
   * Move focus to Cancel when the strip opens, for a strip that replaces the control that
   * opened it (focus would otherwise fall to the page). Cancel, never the confirm button, so a
   * stray Enter is still not the decision.
   */
  focusOnOpen?: boolean;
}) {
  const cancelRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (focusOnOpen) cancelRef.current?.focus();
  }, [focusOnOpen]);
  return (
    <section
      aria-label={title}
      data-testid={testId}
      className="space-y-2 rounded-md border border-status-warn/40 bg-status-warn/10 p-3"
      onKeyDown={(event) => {
        if (event.key !== "Escape" || event.nativeEvent.isComposing) return;
        event.stopPropagation();
        onCancel();
      }}
    >
      <p className="font-medium text-sm">{title}</p>
      <div className="text-sm">{children}</div>
      <div className="flex flex-wrap gap-2">
        <Button size="sm" variant={confirmVariant} onClick={onConfirm}>
          {confirmLabel}
        </Button>
        <Button ref={cancelRef} size="sm" variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </section>
  );
}
