import { AlertTriangle, CheckCircle2, MinusCircle } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import type { ImportResult, ImportSummary } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

/**
 * What a batch of files did, file by file.
 *
 * The summary line answers "did that work"; the table answers the question that
 * actually gets asked, which is *which* of the fourteen files in that folder
 * did not land and why. A batch never aborts — a file that failed comes back in
 * `items` with `status: "failed"` beside the ones that worked — so the list is
 * the only place that difference is visible.
 *
 * Collapsed by default: dropping one file and being handed a table is noise,
 * and the summary is the whole answer for the common case.
 */

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
          <Link className="underline underline-offset-2" to={`/shots/${result.shot_id}`}>
            shot {result.device_id ?? result.shot_id}
          </Link>
        ) : result.label ? (
          // The version, not the page: a profile loaded from a file is on no
          // machine, so the mirror table above it would not list it at all.
          <Link
            className="underline underline-offset-2"
            to={
              result.profile_version_id
                ? `/profiles#version-${result.profile_version_id}`
                : "/profiles"
            }
          >
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

export function ImportResults({ summary }: { summary: ImportSummary }) {
  const [open, setOpen] = useState(false);
  const landed = summary.created + summary.updated;

  return (
    <div className="w-full space-y-2" data-testid="import-result">
      <div className="flex flex-wrap items-center gap-2 text-muted-foreground text-xs">
        <span>
          {landed} imported · {summary.skipped} skipped · {summary.failed} failed
        </span>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="h-6 px-2 text-xs"
          aria-expanded={open}
          data-testid="import-result-toggle"
          onClick={() => setOpen((current) => !current)}
        >
          {open ? "Hide files" : "Show files"}
        </Button>
      </div>

      {open ? (
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
      ) : null}
    </div>
  );
}
