import { useEffect, useId, useState } from "react";
import { ApiClientError } from "@/api/client";
import type { BoardRow, DraftPreview } from "@/api/types";
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
import { usePreviewDraft } from "@/hooks/useDrafts";
import { useDuplicateProfile } from "@/hooks/useDuplicate";
import { useSingleFlight } from "@/hooks/useSingleFlight";
import { noAnswerSentence } from "@/lib/mutations";
import { cn } from "@/lib/utils";

/**
 * Duplicate Profile: a new profile made from one version, under a name the person types, with the
 * document edited by hand if they like. One dialog for the Profiles page (a version's Duplicate
 * Profile) and New Set (a profile that already has a Set).
 *
 * **The name lives in the Name field alone.** It starts empty and must be filled; the original's
 * name is in the title, for reference. The JSON below has no `label`: the field is the one place
 * the name is set, and whatever the document says about a name is replaced by it. A duplicate is
 * always a new profile, so the original's own name is refused like any other taken name.
 *
 * Saving adds the profile to the list, switched on (the next sync with Writes on puts it on the
 * machine); there is no proposal to approve afterwards.
 *
 * The validation is done by the **server**, not by a copy of the rules in the browser. Both halves
 * of it — the strict schema and the safety policy — are the same code the push path runs, and a
 * second implementation here would eventually disagree with the one that matters. The cost is a
 * request per pause in typing, which for a text box a person edits for thirty seconds is nothing.
 *
 * Three outcomes, kept apart because they have different fixes: the document is not a profile
 * (schema errors), it is a profile the policy refuses (violations), or it is a profile the policy
 * would move (clamps). A duplicate must brew what it says, so a clamp blocks the save too.
 */

/** How long to wait after a keystroke before asking the server. */
const DEBOUNCE_MS = 400;

const FIELD = cn(
  "h-8 w-full rounded-md border border-input bg-background px-2 text-sm",
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
);

/** What "the same name" means to the server: stripped, case ignored. */
function nameKey(name: string): string {
  return name.trim().toLowerCase();
}

/** The document as the editor shows it: everything but the name, which has its own field. */
function withoutLabel(document: Record<string, unknown>): string {
  const { label: _label, ...rest } = document;
  return JSON.stringify(rest, null, 2);
}

type Answer =
  | { kind: "none" }
  | { kind: "taken"; name: string; sentence: string }
  | { kind: "other"; sentence: string };

export function DuplicateProfileDialog({
  open,
  onOpenChange,
  baseVersionId,
  label,
  document,
  onSaved,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  baseVersionId: number;
  /** The original's name, shown in the title for reference. */
  label: string;
  /** The version's stored document, as the starting text. */
  document: Record<string, unknown>;
  /** The new profile, once it is on the list (the dialog closes itself). */
  onSaved?: (row: BoardRow) => void;
}) {
  const ids = { name: useId() };
  const [name, setName] = useState("");
  const [text, setText] = useState(() => withoutLabel(document));
  const [preview, setPreview] = useState<DraftPreview | null>(null);
  const [localError, setLocalError] = useState<string | null>(null);
  const [answer, setAnswer] = useState<Answer>({ kind: "none" });
  // Destructured because `mutateAsync` is referentially stable in TanStack Query v5 while the
  // mutation object is not: depending on the object would re-run the debounce effect on every
  // status change, which is once per keystroke's own result.
  const { mutateAsync: validateDocument } = usePreviewDraft();
  const duplicate = useDuplicateProfile();
  const once = useSingleFlight();

  // Re-seed when the dialog opens on a different version: the component may be mounted once and
  // reused, so the initial state above only runs for the first profile somebody opens.
  useEffect(() => {
    if (open) {
      setName("");
      setText(withoutLabel(document));
      setPreview(null);
      setLocalError(null);
      setAnswer({ kind: "none" });
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
      if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
        setLocalError("a profile is a JSON object");
        setPreview(null);
        return;
      }
      setLocalError(null);
      // Validated under the original's name: the name is the field's question, not the
      // document's, so an empty field does not show as a schema error here.
      validateDocument({ baseVersionId, profile: { ...parsed, label } })
        .then(setPreview)
        .catch(() => setPreview(null));
    }, DEBOUNCE_MS);
    return () => clearTimeout(handle);
  }, [open, text, baseVersionId, label, validateDocument]);

  const clamps = preview?.clamp_changes ?? [];
  const documentValid = localError === null && preview?.valid === true && clamps.length === 0;
  const taken = answer.kind === "taken" ? answer : null;
  // The same name again would be refused again: the button waits for a different one.
  const nameRefused = taken !== null && nameKey(name) === nameKey(taken.name);
  const blocked = name.trim() === "" || nameRefused || !documentValid;
  const pending = duplicate.isPending && duplicate.variables?.versionId === baseVersionId;

  const submit = () =>
    once((release) => {
      const profile = { ...(JSON.parse(text) as Record<string, unknown>), label: name.trim() };
      duplicate.mutate(
        { versionId: baseVersionId, body: { name: name.trim(), profile } },
        {
          onSuccess: (row) => {
            onOpenChange(false);
            onSaved?.(row);
          },
          onError: (error) => {
            if (!(error instanceof ApiClientError)) {
              setAnswer({ kind: "other", sentence: noAnswerSentence("the Profiles page") });
            } else if (error.code === "PROFILE_NAME_TAKEN") {
              setAnswer({ kind: "taken", name, sentence: error.sentence });
            } else {
              setAnswer({ kind: "other", sentence: error.sentence });
            }
          },
          onSettled: release,
        },
      );
    });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Duplicate {label}</DialogTitle>
          <DialogDescription>
            A new profile with the name you give it, added to your profiles and switched on: the
            next sync puts it on the machine. Edit the JSON first if you like. It goes through the
            same schema and the same safety policy as anything a model writes.
          </DialogDescription>
        </DialogHeader>

        <form
          className="space-y-3"
          onSubmit={(event) => {
            event.preventDefault();
            if (blocked || pending) return;
            submit();
          }}
        >
          <div>
            <label htmlFor={ids.name} className="mb-1 block font-medium text-sm">
              Name
            </label>
            <input
              id={ids.name}
              className={FIELD}
              value={name}
              required
              maxLength={200}
              autoComplete="off"
              aria-invalid={taken ? true : undefined}
              aria-describedby={taken ? `${ids.name}-note` : undefined}
              data-testid="duplicate-profile-name"
              onChange={(event) => setName(event.target.value)}
            />
            {taken ? (
              <p
                id={`${ids.name}-note`}
                className="mt-1 text-destructive text-xs"
                role="alert"
                data-testid="duplicate-profile-name-taken"
              >
                {taken.sentence}
              </p>
            ) : null}
          </div>

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

          {clamps.length > 0 ? (
            <div data-testid="editor-clamps">
              <p className="font-medium text-destructive text-sm">
                Outside the safety bounds: bring these into range first
              </p>
              <ul className="mt-1 space-y-0.5">
                {clamps.map((clamp) => (
                  <li key={clamp.path} className="text-sm">
                    <span className="font-mono text-muted-foreground text-xs">{clamp.path}</span>{" "}
                    {clamp.before} → {clamp.after}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          {documentValid ? (
            <p className="text-muted-foreground text-sm" data-testid="editor-valid">
              Valid.
            </p>
          ) : null}

          {answer.kind === "other" ? (
            <p className="text-destructive text-sm" role="alert" data-testid="duplicate-refused">
              {answer.sentence}
            </p>
          ) : null}

          <DialogFooter>
            <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={blocked || pending} data-testid="save-duplicate">
              {pending ? "Saving..." : "Save"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
