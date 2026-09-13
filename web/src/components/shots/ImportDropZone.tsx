import { FileJson } from "lucide-react";
import { type ChangeEvent, type DragEvent, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { useImportFiles } from "@/hooks/useImport";
import { cn } from "@/lib/utils";

/**
 * The other way in: files.
 *
 * The machine keeps about 300 KB of history and deletes its oldest shots to
 * make room, so for anything from before this box existed the exports somebody
 * saved out of the machine's own web UI are the only copy left. That belongs on
 * the front page next to the pull button, because "get my shots in" is one
 * question with two answers and burying one of them on another page made it
 * look like the archive only did machines.
 *
 * A strip rather than the Import page's big target: this is a thing you drop a
 * file on while looking at your list. The per-file detail — which of the
 * fourteen files in that folder did not land, and why — is what the Import page
 * is for, and the result line links there.
 */

/** Shot exports, profile exports, and zips of either. `.slog` is the raw file. */
const ACCEPT = ".json,.slog,.zip,application/json,application/zip";

export function ImportDropZone() {
  const importFiles = useImportFiles();
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const summary = importFiles.data;
  // `dragenter`/`dragleave` fire for every child the pointer crosses, so a
  // boolean flickers the highlight off and on as the file passes over the
  // icon, the text and the button. Counting entries against leaves is the
  // standard answer: the strip is "dragged over" while the depth is positive.
  const depth = useRef(0);

  function setDepth(next: number) {
    depth.current = Math.max(0, next);
    setDragging(depth.current > 0);
  }

  function send(files: File[]) {
    if (files.length === 0) return;
    importFiles.mutate({ files });
  }

  function onDrop(event: DragEvent<HTMLElement>) {
    event.preventDefault();
    setDepth(0);
    send(Array.from(event.dataTransfer.files));
  }

  function onPick(event: ChangeEvent<HTMLInputElement>) {
    send(Array.from(event.target.files ?? []));
    // Cleared, so choosing the same file twice fires a change event both
    // times — which here is a deliberate retry, not a mistake.
    event.target.value = "";
  }

  return (
    <section
      onDrop={onDrop}
      onDragEnter={() => setDepth(depth.current + 1)}
      onDragOver={(event) => {
        // Without this the browser refuses the drop; it says nothing about the
        // highlight, which the enter/leave pair owns.
        event.preventDefault();
      }}
      onDragLeave={() => setDepth(depth.current - 1)}
      // A <section> with a label rather than a <div>: a drop target is not a
      // button and must not answer Enter like one. The keyboard and phone path
      // to the same thing is the button inside it.
      aria-label="Drop export files to import"
      data-testid="shots-dropzone"
      className={cn(
        "flex flex-wrap items-center gap-x-3 gap-y-2 rounded-lg border border-border border-dashed px-3 py-2 text-sm transition-colors",
        dragging && "border-primary bg-muted/50",
      )}
    >
      <FileJson className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
      <span className="text-muted-foreground">
        Drop exported shots or profiles here — .json, .slog or a zip of either.
      </span>
      <Button
        type="button"
        variant="outline"
        size="sm"
        onClick={() => inputRef.current?.click()}
        disabled={importFiles.isPending}
      >
        {importFiles.isPending ? "Importing…" : "Choose files"}
      </Button>
      <input
        ref={inputRef}
        type="file"
        multiple
        accept={ACCEPT}
        className="hidden"
        onChange={onPick}
        data-testid="shots-import-input"
        aria-label="Export files to import"
      />
      {summary ? (
        <span className="ml-auto text-muted-foreground text-xs" data-testid="import-result">
          {summary.created + summary.updated} imported · {summary.skipped} skipped ·{" "}
          {summary.failed} failed ·{" "}
          <Link to="/import" className="underline underline-offset-2">
            see the file list
          </Link>
        </span>
      ) : null}
    </section>
  );
}
