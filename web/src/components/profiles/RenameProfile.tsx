import { AlertTriangle } from "lucide-react";
import { useId, useRef, useState } from "react";
import { ApiClientError } from "@/api/client";
import { Button } from "@/components/ui/button";
import { useRenameProfile } from "@/hooks/useBoard";
import { noAnswerSentence } from "@/lib/mutations";

/** The longest name the server takes (as the Approve field). */
const NAME_MAX = 200;

/** What the Name field says about when the machine hears of the new name. */
export const RENAME_SENTENCE =
  "The machine gets the new name at the next sync. Shots keep the name the machine showed when they were brewed.";

/**
 * The top of an open profile: its name, with **Rename** beside it. Rename turns the line into the
 * Name field (`editing`, owned by the row so the conflict's "Rename to …" can open it), with
 * Cancel and Rename under it. Enter submits and Escape cancels. Rename waits while the field is
 * empty or holds the name the profile has already (a change of case alone is a rename). A refusal
 * is the server's own sentence under the field, and the field stays open with what was typed.
 *
 * Nothing is sent to the machine: the name is the profile's, and the next sync gives the machine's
 * file the name. When a Recipe brews the profile, a line under the field says what the machine
 * will show (the name and the Recipe's version); the version is never part of the name typed. The
 * line keeps showing the machine's current name, not one that follows what is typed. A pinned copy
 * has the same control, since a pin freezes the brew and not the name.
 */
export function RenameProfile({
  rowId,
  name,
  machineName,
  editing,
  onEditingChange,
}: {
  rowId: number;
  name: string;
  /** What the machine shows: the name and the Recipe's version when a Recipe brews the profile. */
  machineName: string;
  editing: boolean;
  onEditingChange: (editing: boolean) => void;
}) {
  if (editing) {
    return (
      <NameField
        rowId={rowId}
        name={name}
        machineName={machineName}
        onDone={() => onEditingChange(false)}
      />
    );
  }
  return (
    <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
      <h3 className="min-w-0 break-words font-medium text-sm" data-testid="profile-name">
        {name}
      </h3>
      <Button
        type="button"
        size="sm"
        variant="outline"
        data-testid="rename-open"
        aria-label={`Rename ${name}`}
        onClick={() => onEditingChange(true)}
      >
        Rename
      </Button>
    </div>
  );
}

/** The Name field. Mounted only while it is open, so it always starts from the profile's name. */
function NameField({
  rowId,
  name,
  machineName,
  onDone,
}: {
  rowId: number;
  name: string;
  machineName: string;
  onDone: () => void;
}) {
  const rename = useRenameProfile();
  const [typed, setTyped] = useState(name);
  const id = useId();
  const helpId = useId();
  const machineId = useId();
  const showsMachineName = machineName !== name;
  const clicking = useRef(false);
  const trimmed = typed.trim();
  const problem = rename.isError ? refusal(rename.error) : null;

  const submit = () => {
    if (clicking.current || trimmed === "" || trimmed === name) return;
    clicking.current = true;
    rename.mutate(
      { rowId, label: trimmed },
      {
        onSuccess: onDone,
        onSettled: () => {
          clicking.current = false;
        },
      },
    );
  };

  return (
    <form
      className="min-w-0 space-y-1"
      data-testid="rename-form"
      onSubmit={(event) => {
        event.preventDefault();
        submit();
      }}
      onKeyDown={(event) => {
        if (event.key === "Escape") {
          event.stopPropagation();
          onDone();
        }
      }}
    >
      <label htmlFor={id} className="block font-medium text-sm">
        Name
      </label>
      <input
        id={id}
        // The field opens with the cursor in it: the person pressed Rename to type a name.
        // biome-ignore lint/a11y/noAutofocus: the field appears because the person asked for it
        autoFocus
        value={typed}
        maxLength={NAME_MAX}
        autoComplete="off"
        aria-invalid={problem !== null}
        aria-describedby={showsMachineName ? `${helpId} ${machineId}` : helpId}
        data-testid="rename-name"
        className="h-8 w-full min-w-0 rounded-md border border-input bg-background px-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring aria-[invalid=true]:border-destructive"
        onChange={(event) => {
          setTyped(event.target.value);
          if (rename.isError) rename.reset();
        }}
      />
      <p id={helpId} className="break-words text-muted-foreground text-xs">
        {RENAME_SENTENCE}
      </p>
      {showsMachineName ? (
        <p
          id={machineId}
          className="break-words text-muted-foreground text-xs"
          data-testid="rename-machine-name"
        >
          {`On the machine: ${machineName} — the version follows the Recipe.`}
        </p>
      ) : null}
      {problem !== null ? (
        <p
          className="flex items-start gap-1.5 text-destructive text-xs"
          role="alert"
          data-testid="rename-problem"
        >
          <AlertTriangle className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
          <span className="min-w-0 break-words">{problem}</span>
        </p>
      ) : null}
      <div className="flex flex-wrap justify-end gap-2 pt-1">
        <Button
          type="button"
          size="sm"
          variant="ghost"
          data-testid="rename-cancel"
          onClick={onDone}
        >
          Cancel
        </Button>
        <Button
          type="submit"
          size="sm"
          data-testid="rename-submit"
          disabled={trimmed === "" || trimmed === name || rename.isPending}
        >
          Rename
        </Button>
      </div>
    </form>
  );
}

/** The server's sentence, or the one honest thing to say when no answer came back. */
function refusal(error: Error): string {
  return error instanceof ApiClientError && error.status !== undefined
    ? error.sentence
    : noAnswerSentence("the profile");
}
