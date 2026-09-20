import { useId, useState } from "react";
import type { VersionEvidence as Evidence } from "@/api/types";
import { Button } from "@/components/ui/button";
import { useVocabulary } from "@/hooks/useCatalog";
import { differenceCell, evidenceSideSummary, meanCell, verdictSentence } from "@/lib/sets";

/**
 * The numbers a version's prediction is graded on, as a table.
 *
 * All of this version's counted shots against all of the compared version's,
 * measure by measure, each difference marked beyond the Set's spread or inside
 * it and saying what it was held against. Never one chosen shot on either side:
 * a prediction graded against the best shot of the version before it is graded
 * against a memory.
 *
 * Closed by default, because most entries in a log are history somebody is
 * scrolling past. Open by default on the one entry that is still a live
 * question: a prediction nobody has graded yet, on a version that has shots to
 * grade it with.
 *
 * A disclosure in the house pattern — the region is always in the document and
 * toggled with `hidden`, so `aria-controls` names something a reader can reach
 * — and every verdict is a word rather than a colour, because this is the cell
 * somebody acts on.
 */
export function VersionEvidence({
  evidence,
  versionNo,
  defaultOpen = false,
}: {
  evidence: Evidence;
  versionNo: number;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const regionId = useId();
  const vocab = useVocabulary();
  const terms = new Map((vocab.data?.spread_measures ?? []).map((term) => [term.value, term]));
  const other = evidence.other ?? null;

  return (
    <div data-testid="version-evidence" data-version={versionNo}>
      <Button
        type="button"
        size="sm"
        variant="ghost"
        aria-expanded={open}
        aria-controls={regionId}
        onClick={() => setOpen((shown) => !shown)}
      >
        Evidence
      </Button>
      <div id={regionId} hidden={!open} className="mt-2 space-y-2">
        {open ? (
          <>
            <table className="w-full text-left text-sm" data-testid="evidence-table">
              <caption className="sr-only">
                {other
                  ? `What v${versionNo}'s shots did, beside v${other.version_no}'s`
                  : `What v${versionNo}'s shots did. This prediction compares against nothing.`}
              </caption>
              <thead>
                <tr className="text-muted-foreground text-xs">
                  <th scope="col" className="py-1 pr-3 font-normal">
                    Measure
                  </th>
                  <th scope="col" className="py-1 pr-3 font-normal">
                    v{versionNo}
                  </th>
                  {other ? (
                    <>
                      <th scope="col" className="py-1 pr-3 font-normal">
                        v{other.version_no}
                      </th>
                      <th scope="col" className="py-1 pr-3 font-normal">
                        Difference
                      </th>
                      <th scope="col" className="py-1 font-normal">
                        What it shows
                      </th>
                    </>
                  ) : null}
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {evidence.measures.map((row) => {
                  const term = terms.get(row.measure);
                  return (
                    <tr key={row.measure} data-testid="evidence-row" data-measure={row.measure}>
                      <th scope="row" className="py-1 pr-3 font-normal text-muted-foreground">
                        {term?.label ?? row.measure}
                      </th>
                      <td className="py-1 pr-3 tabular-nums">{meanCell(row.this, term)}</td>
                      {other ? (
                        <>
                          <td className="py-1 pr-3 tabular-nums">
                            {row.other ? meanCell(row.other, term) : "not recorded"}
                          </td>
                          <td className="py-1 pr-3 tabular-nums">{differenceCell(row, term)}</td>
                          <td className="py-1" data-testid="evidence-verdict">
                            {verdictSentence(row, term)}
                          </td>
                        </>
                      ) : null}
                    </tr>
                  );
                })}
              </tbody>
            </table>
            {/* The one sentence that ties the verdicts to the Spread block
                above: without it "beyond the spread" is a word the page uses
                and never explains. It says what the code does — two standard
                errors, floored — rather than a gesture at statistics. */}
            <p className="text-muted-foreground text-xs" data-testid="evidence-yardstick">
              Each difference is held against about two standard errors of itself — this Set's
              spread for that measure, scaled to how many shots are on each side — and never against
              less than that measure's floor. While a measure's spread is not measured yet, the
              floor is the whole yardstick.
            </p>
            {/* Plain facts with nothing held against them: how the cups went
                and how they were labelled. They are words, not quantities, and
                a verdict on them would be arithmetic pretending. */}
            <ul className="text-muted-foreground text-xs" data-testid="evidence-counts">
              <li data-side="this">
                v{evidence.this.version_no}: {evidenceSideSummary(evidence.this)}
              </li>
              {other ? (
                <li data-side="other">
                  v{other.version_no}: {evidenceSideSummary(other)}
                </li>
              ) : (
                <li data-side="none">
                  Compared against nothing — these are this version's own numbers.
                </li>
              )}
            </ul>
          </>
        ) : null}
      </div>
    </div>
  );
}
