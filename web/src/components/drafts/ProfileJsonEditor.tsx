import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import type { DraftPreview } from "@/api/types";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/textarea";
import { useCreateDraft, usePreviewDraft } from "@/hooks/useDrafts";

/**
 * Edit a profile version by hand, validated as you type.
 *
 * The validation is done by the **server**, not by a copy of the rules in the
 * browser. Both halves of it — the strict schema and the safety policy — are
 * the same code the push path runs, and a second implementation here would
 * eventually disagree with the one that matters. The cost is a request per
 * pause in typing, which for a text box a person edits for thirty seconds is
 * nothing.
 *
 * Three outcomes, kept apart because they have different fixes: the document is
 * not a profile (schema errors), it is a profile the policy refuses
 * (violations), or it is a profile the policy would quietly move (clamps —
 * allowed, but only once somebody has seen the list).
 */

/** How long to wait after a keystroke before asking the server. */
const DEBOUNCE_MS = 400;

export function ProfileJsonEditor({
  open,
  onOpenChange,
  baseVersionId,
  label,
  document,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  baseVersionId: number;
  label: string;
  /** The version's stored document, as the starting text. */
  document: Record<string, unknown>;
}) {
  const [text, setText] = useState(() => JSON.stringify(document, null, 2));
  const [preview, setPreview] = useState<DraftPreview | null>(null);
  const [localError, setLocalError] = useState<string | null>(null);
  // Destructured because `mutateAsync` is referentially stable in TanStack
  // Query v5 while the mutation object is not: depending on the object would
  // re-run the debounce effect on every status change, which is once per
  // keystroke's own result.
  const { mutateAsync: validateDocument } = usePreviewDraft();
  const create = useCreateDraft();
  const navigate = useNavigate();

  // Re-seed when the dialog opens on a different version: the component is
  // mounted once by the page and reused, so the initial state above only runs
  // for the first profile somebody opens.
  useEffect(() => {
    if (open) {
      setText(JSON.stringify(document, null, 2));
      setPreview(null);
      setLocalError(null);
    }
  }, [open, document]);

  useEffect(() => {
    if (!open) return undefined;
    const handle = setTimeout(() => {
      let parsed: unknown;
      try {
        parsed = JSON.parse(text);
      } catch (error) {
        setLocalError(error instanceof Error ? error.message : "not valid JSON");
        setPreview(null);
        return;
      }
      setLocalError(null);
      validateDocument({ baseVersionId, profile: parsed as Record<string, unknown> })
        .then(setPreview)
        .catch(() => setPreview(null));
    }, DEBOUNCE_MS);
    return () => clearTimeout(handle);
  }, [open, text, baseVersionId, validateDocument]);

  const valid = localError === null && preview?.valid === true;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Edit {label} as a draft</DialogTitle>
          <DialogDescription>
            Saved as a new draft, never over the profile on the machine. It goes through the same
            schema and the same safety policy as anything a model writes.
          </DialogDescription>
        </DialogHeader>

        <Textarea
          className="min-h-72 font-mono text-xs"
          spellCheck={false}
          value={text}
          aria-label="Profile JSON"
          onChange={(event) => setText(event.target.value)}
        />

        {localError ? (
          <p className="text-destructive text-sm" data-testid="editor-json-error">
            {localError}
          </p>
        ) : null}

        {preview?.schema_errors && preview.schema_errors.length > 0 ? (
          <div data-testid="editor-schema-errors">
            <p className="font-medium text-destructive text-sm">Not a valid profile</p>
            <ul className="mt-1 space-y-0.5">
              {preview.schema_errors.map((message) => (
                <li key={message} className="font-mono text-destructive text-xs">
                  {message}
                </li>
              ))}
            </ul>
          </div>
        ) : null}

        {preview?.violations && preview.violations.length > 0 ? (
          <div data-testid="editor-violations">
            <p className="font-medium text-destructive text-sm">
              The safety policy will not allow this
            </p>
            <ul className="mt-1 space-y-0.5">
              {preview.violations.map((violation) => (
                <li key={violation.path} className="text-destructive text-xs">
                  <span className="font-mono">{violation.path}</span> — {violation.message}
                </li>
              ))}
            </ul>
          </div>
        ) : null}

        {preview?.clamp_changes && preview.clamp_changes.length > 0 ? (
          <div data-testid="editor-clamps">
            <p className="font-medium text-sm">These would be moved into range</p>
            <ul className="mt-1 space-y-0.5">
              {preview.clamp_changes.map((clamp) => (
                <li key={clamp.path} className="text-sm">
                  <span className="font-mono text-muted-foreground text-xs">{clamp.path}</span>{" "}
                  {clamp.before} → {clamp.after}
                </li>
              ))}
            </ul>
          </div>
        ) : null}

        {valid ? (
          <p className="text-muted-foreground text-sm" data-testid="editor-valid">
            Valid. It would be saved as{" "}
            <span className="font-medium">
              {String((preview?.profile as Record<string, unknown> | null)?.label ?? label)}
            </span>
            .
          </p>
        ) : null}

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            disabled={!valid || create.isPending}
            data-testid="save-as-draft"
            onClick={async () => {
              const draft = await create.mutateAsync({
                base_version_id: baseVersionId,
                profile: JSON.parse(text) as Record<string, unknown>,
                change_summary: "Edited by hand.",
              });
              if (draft) {
                onOpenChange(false);
                navigate("/profiles#staged");
              }
            }}
          >
            {create.isPending ? "Saving..." : "Save as a draft"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
