import type { ReactNode } from "react";
import { Button } from "@/components/ui/button";

/**
 * The inline "are you sure" under a Sync page action that writes to the machine.
 *
 * Inline rather than a dialog: the question sits directly under the list it is
 * about, so the shots being deleted or sent stay on screen while somebody
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
}: {
  title: string;
  children: ReactNode;
  confirmLabel: string;
  onConfirm: () => void;
  onCancel: () => void;
  testId: string;
}) {
  return (
    <section
      aria-label={title}
      data-testid={testId}
      className="space-y-2 rounded-md border border-status-warn/40 bg-status-warn/10 p-3"
    >
      <p className="font-medium text-sm">{title}</p>
      <div className="text-sm">{children}</div>
      <div className="flex flex-wrap gap-2">
        <Button size="sm" variant="destructive" onClick={onConfirm}>
          {confirmLabel}
        </Button>
        <Button size="sm" variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </section>
  );
}
