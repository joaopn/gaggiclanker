import { useId, useState } from "react";
import { ApiClientError } from "@/api/client";
import type { PinPreview, SetVersionRow } from "@/api/types";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { usePinPreview, usePinVersion } from "@/hooks/usePins";
import { useSingleFlight } from "@/hooks/useSingleFlight";
import { noAnswerSentence } from "@/lib/mutations";
import { handSettingsWords } from "@/lib/sets";
import { cn } from "@/lib/utils";

const FIELD = cn(
  "h-8 w-full rounded-md border border-input bg-background px-2 text-sm",
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
);

/** The id of a version's pinned marker, where focus goes once the pin has landed. */
export function pinnedMarkerId(versionId: number): string {
  return `pinned-marker-${versionId}`;
}

/** What "the same name" means to the server: stripped, case ignored. */
function nameKey(name: string): string {
  return name.trim().toLowerCase();
}

/** The id the "Pin to the machine" button of a version carries, so the dialog can give focus back. */
export function pinButtonId(versionId: number): string {
  return `pin-version-${versionId}`;
}

/**
 * "Pin v1.2 to the machine": the question the pin's own preview answers, and the pin.
 *
 * It opens with the preview, which is the server's word on what would happen. The default state
 * says what the machine gets. A name that is taken shows the server's sentence and a Name
 * field, prefilled and editable, and only then; any other refusal shows the server's sentence
 * and Close, since there is nothing for the person to change. A refusal that arrives on submit
 * (somebody else pinned or named something in the meantime) is shown in place, in the server's
 * words, and the dialog stays open. The web words nothing a refusal says.
 */
export function PinDialog({
  setId,
  version,
  open,
  onOpenChange,
  onPinned,
}: {
  setId: number;
  version: SetVersionRow;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** The pin was made: the dialog closes, and the caller can move focus to the result. */
  onPinned?: () => void;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className="max-h-[90vh] overflow-y-auto sm:max-w-lg"
        data-testid="pin-dialog"
        onCloseAutoFocus={(event) => {
          const opener = document.getElementById(pinButtonId(version.id));
          if (opener) {
            event.preventDefault();
            opener.focus();
          }
        }}
      >
        <DialogHeader>
          <DialogTitle>Pin {version.version_label} to the machine</DialogTitle>
          <DialogDescription className="sr-only">
            Keep {version.version_label} on the machine as a profile of its own.
          </DialogDescription>
        </DialogHeader>
        {/* Mounted only while open: every opening asks the preview afresh and starts with a
            clean form, and a refusal from the last opening is not carried over. */}
        {open ? (
          <PinBody
            key={version.id}
            setId={setId}
            version={version}
            onClose={() => onOpenChange(false)}
            onPinned={onPinned}
          />
        ) : null}
      </DialogContent>
    </Dialog>
  );
}

function PinBody({
  setId,
  version,
  onClose,
  onPinned,
}: {
  setId: number;
  version: SetVersionRow;
  onClose: () => void;
  onPinned?: () => void;
}) {
  const preview = usePinPreview(setId, version.id);
  if (preview.isPending) {
    return <Skeleton className="h-20 w-full" data-testid="pin-checking" />;
  }
  if (preview.isError || !preview.data) {
    return (
      <>
        <p className="text-sm" role="alert" data-testid="pin-preview-error">
          {preview.error instanceof ApiClientError
            ? preview.error.sentence
            : "The pin could not be checked right now."}
        </p>
        <DialogFooter>
          <Button type="button" variant="outline" onClick={onClose}>
            Close
          </Button>
        </DialogFooter>
      </>
    );
  }
  return (
    <PinForm
      setId={setId}
      version={version}
      preview={preview.data}
      onClose={onClose}
      onPinned={onPinned}
    />
  );
}

/** What the form stands on: the server's word, from the preview or from the last answer. */
type Standing =
  | { kind: "open" }
  | { kind: "taken"; name: string; sentence: string }
  | { kind: "refused"; sentence: string };

function standingOf(preview: PinPreview): Standing {
  if (preview.taken) return { kind: "taken", name: preview.name, sentence: preview.refused ?? "" };
  if (preview.refused != null) return { kind: "refused", sentence: preview.refused };
  return { kind: "open" };
}

function PinForm({
  setId,
  version,
  preview,
  onClose,
  onPinned,
}: {
  setId: number;
  version: SetVersionRow;
  preview: PinPreview;
  onClose: () => void;
  onPinned?: () => void;
}) {
  const pin = usePinVersion();
  const once = useSingleFlight();
  const nameId = useId();
  const [name, setName] = useState(preview.name);
  // Held in state, so an answer stays on screen while the next request is in flight (the
  // mutation's own error is cleared the moment it starts again). The Name field exists in
  // exactly one state: the name is taken, as the preview said or as the answer to a pin said.
  const [standing, setStanding] = useState<Standing>(() => standingOf(preview));
  const [unreachable, setUnreachable] = useState(false);

  const submit = () =>
    once((release) => {
      const label = standing.kind === "taken" ? name.trim() : undefined;
      setUnreachable(false);
      pin.mutate(
        { setId, versionId: version.id, label },
        {
          onSuccess: () => {
            onClose();
            onPinned?.();
          },
          onError: (error) => {
            if (!(error instanceof ApiClientError)) {
              // Nothing the server said, so nothing known about the pin: the card behind shows the truth
              // once the Set is re-read, and the form stays for another try.
              setUnreachable(true);
              return;
            }
            const taken = error.code === "PIN_NAME_TAKEN";
            const takenName = (error.details as { name?: unknown } | null | undefined)?.name;
            setStanding(
              taken
                ? {
                    kind: "taken",
                    name: typeof takenName === "string" ? takenName : name,
                    sentence: error.sentence,
                  }
                : { kind: "refused", sentence: error.sentence },
            );
          },
          onSettled: release,
        },
      );
    });

  if (standing.kind === "refused") {
    return (
      <>
        <p className="text-sm" role="alert" data-testid="pin-refused">
          {standing.sentence}
        </p>
        <DialogFooter>
          <Button type="button" variant="outline" onClick={onClose}>
            Close
          </Button>
        </DialogFooter>
      </>
    );
  }

  const taken = standing.kind === "taken" ? standing : null;
  // The same name again would be refused again: the button waits for a different one.
  const blank = taken !== null && name.trim() === "";
  const unchanged = taken !== null && nameKey(name) === nameKey(taken.name);
  const hands = handSettingsWords(version);
  return (
    <form
      className="space-y-3"
      onSubmit={(event) => {
        event.preventDefault();
        if (blank || unchanged) return;
        submit();
      }}
    >
      {taken ? (
        <>
          <p className="text-sm" role="alert" data-testid="pin-name-taken">
            {taken.sentence}
          </p>
          <div>
            <label htmlFor={nameId} className="mb-1 block text-muted-foreground text-xs">
              Name
            </label>
            <input
              id={nameId}
              className={FIELD}
              value={name}
              maxLength={200}
              onChange={(event) => setName(event.target.value)}
            />
          </div>
        </>
      ) : (
        <p className="text-sm" data-testid="pin-explained">
          The machine gets a profile called "{preview.name}", frozen at this version's profile.
          Shots brewed on it are filed under {version.version_label}.
          {hands
            ? ` ${capital(hands)} stay ${version.version_label}'s: set them at the grinder and scale.`
            : ""}
        </p>
      )}
      {unreachable ? (
        <p className="text-sm text-destructive" role="alert" data-testid="pin-unreachable">
          {noAnswerSentence("the card")}
        </p>
      ) : null}
      <DialogFooter>
        <Button type="button" variant="ghost" onClick={onClose}>
          Cancel
        </Button>
        <Button type="submit" disabled={pin.isPending || blank || unchanged}>
          Pin
        </Button>
      </DialogFooter>
    </form>
  );
}

function capital(text: string): string {
  return text.slice(0, 1).toUpperCase() + text.slice(1);
}
