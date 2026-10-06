import { useId, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

/** What the route accepts of a reason, so the field stops where the server would refuse. */
export const REASON_MAX = 300;

/**
 * "Reject", with the one line of why a person may add.
 *
 * The reason is optional and is what the proposing conversation is told, so it is asked for
 * once, inline, and never required. Enter submits; a second press while the request is out is
 * the caller's to ignore (`busy`).
 */
export function ReasonForm({
  label,
  submitLabel = "Reject",
  busy,
  onSubmit,
  onCancel,
  testId,
}: {
  /** What the field is for, for a screen reader: "Why reject the ramp expectation". */
  label: string;
  submitLabel?: string;
  busy: boolean;
  onSubmit: (reason: string) => void;
  onCancel: () => void;
  testId: string;
}) {
  const [reason, setReason] = useState("");
  const id = useId();
  return (
    <form
      className="flex min-w-0 flex-wrap items-center gap-2"
      data-testid={testId}
      onSubmit={(event) => {
        event.preventDefault();
        onSubmit(reason.trim());
      }}
    >
      <label htmlFor={id} className="sr-only">
        {label}
      </label>
      <Input
        id={id}
        value={reason}
        maxLength={REASON_MAX}
        placeholder="Why (optional)"
        className="h-8 min-w-0 flex-1 basis-40"
        onChange={(event) => setReason(event.target.value)}
        // biome-ignore lint/a11y/noAutofocus: the person has just asked to give a reason.
        autoFocus
      />
      <Button type="submit" size="sm" variant="outline" disabled={busy} data-testid="reason-submit">
        {submitLabel}
      </Button>
      <Button type="button" size="sm" variant="ghost" onClick={onCancel}>
        Cancel
      </Button>
    </form>
  );
}
