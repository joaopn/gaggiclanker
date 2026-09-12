import { AlertTriangle, CheckCircle2, FileJson, Import, MinusCircle } from "lucide-react";
import { type ChangeEvent, type DragEvent, useId, useRef, useState } from "react";
import { Link } from "react-router-dom";
import type { ImportResult } from "@/api/types";
import { EmptyState } from "@/components/layout/EmptyState";
import { PageHeader } from "@/components/layout/PageHeader";
import { SectionCard } from "@/components/layout/SectionCard";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { useImportFiles } from "@/hooks/useImport";
import { cn } from "@/lib/utils";

/**
 * Load shots and profiles the machine has already deleted.
 *
 * The device keeps about 300 KB of history and drops the oldest when it runs
 * short, so for anything from before this container existed the files a
 * maintainer saved out of the machine's own web UI are the only copy left.
 * That is why the result list is the page rather than a toast: after dropping
 * a folder on it, the question is always *which* files did not land.
 */

const ACCEPT = ".json,.zip,application/json,application/zip";

function statusBadge(result: ImportResult) {
  if (result.status === "failed") {
    return (
      <Badge variant="destructive" className="gap-1">
        <AlertTriangle className="size-3" aria-hidden="true" />
        failed
      </Badge>
    );
  }
  if (result.status === "skipped") {
    return (
      <Badge variant="outline" className="gap-1">
        <MinusCircle className="size-3" aria-hidden="true" />
        skipped
      </Badge>
    );
  }
  return (
    <Badge variant={result.quarantined ? "secondary" : "default"} className="gap-1">
      <CheckCircle2 className="size-3" aria-hidden="true" />
      {result.status}
    </Badge>
  );
}

function ResultRow({ result }: { result: ImportResult }) {
  return (
    <tr className="border-border border-b last:border-0 align-top">
      <td className="py-2 pr-4">
        <span className="block max-w-[22rem] truncate font-mono text-xs">{result.filename}</span>
        <span className="text-muted-foreground text-xs">{result.kind}</span>
      </td>
      <td className="py-2 pr-4">{statusBadge(result)}</td>
      <td className="py-2 pr-4">
        {result.shot_id ? (
          // The per-shot page arrives later; until then the archive list is
          // where an imported shot is actually visible.
          <Link className="underline underline-offset-2" to="/shots">
            shot {result.device_id ?? result.shot_id}
          </Link>
        ) : result.label ? (
          <Link className="underline underline-offset-2" to="/profiles">
            {result.label}
          </Link>
        ) : (
          <span className="text-muted-foreground">—</span>
        )}
      </td>
      <td className="py-2 text-muted-foreground">{result.message}</td>
    </tr>
  );
}

export function ImportPage() {
  const importFiles = useImportFiles();
  const [replace, setReplace] = useState(false);
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const replaceId = useId();

  const summary = importFiles.data;

  function send(files: File[]) {
    if (files.length === 0) return;
    importFiles.mutate({ files, replace });
  }

  function onDrop(event: DragEvent<HTMLElement>) {
    event.preventDefault();
    setDragging(false);
    send(Array.from(event.dataTransfer.files));
  }

  function onPick(event: ChangeEvent<HTMLInputElement>) {
    send(Array.from(event.target.files ?? []));
    // Clear it, so choosing the same file twice fires a change event both
    // times — which on this page is a deliberate retry, not a mistake.
    event.target.value = "";
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Import"
        subtitle="Shot and profile exports from the machine's own web UI. The archive's way back for shots the device has already deleted."
      />

      <SectionCard
        title="Drop files here"
        description="Shot exports, profile exports, a JSON array of profiles, or a zip of any of those. Files are recognised by what is in them, not by their name."
      >
        <section
          onDrop={onDrop}
          onDragOver={(event) => {
            event.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          // A <section> with a label rather than a <div>: a drop target is not
          // a button and must not answer Enter like one, and the keyboard path
          // to the same thing is the "Choose files" button inside it.
          aria-label="Drop export files to import"
          data-testid="import-dropzone"
          className={cn(
            "flex flex-col items-center justify-center rounded-lg border border-border border-dashed px-6 py-12 text-center transition-colors",
            dragging && "border-primary bg-muted/50",
          )}
        >
          <FileJson className="mb-3 size-8 text-muted-foreground" aria-hidden="true" />
          <p className="font-medium text-sm">Drag exports here</p>
          <p className="mt-1 max-w-prose text-muted-foreground text-sm">
            Up to 50 MB per upload — a few hundred shots. Importing the same shot twice is a no-op.
          </p>
          <div className="mt-4 flex flex-wrap items-center justify-center gap-3">
            <Button
              type="button"
              onClick={() => inputRef.current?.click()}
              disabled={importFiles.isPending}
            >
              {importFiles.isPending ? "Importing..." : "Choose files"}
            </Button>
            <div className="flex items-center gap-2">
              <input
                id={replaceId}
                type="checkbox"
                className="size-4 accent-primary"
                checked={replace}
                onChange={(event) => setReplace(event.target.checked)}
              />
              <Label htmlFor={replaceId} className="text-muted-foreground text-sm">
                Replace shots already in the archive
              </Label>
            </div>
          </div>
          <input
            ref={inputRef}
            type="file"
            multiple
            accept={ACCEPT}
            className="hidden"
            onChange={onPick}
            data-testid="import-input"
            aria-label="Export files to import"
          />
        </section>
      </SectionCard>

      {summary ? (
        <SectionCard
          title="Results"
          description={`${summary.created} created · ${summary.updated} updated · ${summary.skipped} skipped · ${summary.failed} failed`}
        >
          <div className="overflow-x-auto">
            <table className="w-full border-collapse text-left text-sm">
              <thead className="border-border border-b text-muted-foreground text-xs uppercase tracking-wide">
                <tr>
                  <th className="py-2 pr-4 font-medium">File</th>
                  <th className="py-2 pr-4 font-medium">Status</th>
                  <th className="py-2 pr-4 font-medium">In the archive</th>
                  <th className="py-2 font-medium">Detail</th>
                </tr>
              </thead>
              <tbody>
                {summary.items.map((result) => (
                  <ResultRow
                    // Two entries can share a file name — a multi-profile export
                    // reports one row per profile — so what it landed on is part
                    // of the key.
                    key={`${result.filename}:${result.kind}:${
                      result.device_id ?? result.label ?? result.status
                    }`}
                    result={result}
                  />
                ))}
              </tbody>
            </table>
          </div>
        </SectionCard>
      ) : (
        <EmptyState
          icon={Import}
          title="Nothing imported yet"
          description="Export a shot from the machine's web UI (History → a shot → Export) and drop the file here. Profiles export the same way."
        />
      )}
    </div>
  );
}
