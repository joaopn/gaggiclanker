import { useEffect, useId, useRef, useState } from "react";
import type { SetVersionDetail, SetVersionRow } from "@/api/types";
import { Button } from "@/components/ui/button";
import { useSetVersionPrediction } from "@/hooks/useSets";
import { attempt } from "@/lib/mutations";
import { cn } from "@/lib/utils";

/**
 * What a version is expected to do differently, typed before the first shot.
 *
 * Only ever rendered on a version that has no shots: the server refuses the
 * write afterwards, and offering a field that cannot be saved is worse than
 * offering none. Clearing the text removes the prediction, comparison and all,
 * which is the only way to take one back.
 */

const FIELD = cn(
  "w-full rounded-md border border-input bg-background px-2 py-1.5 text-sm",
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
);

export function VersionPredictionEditor({
  setId,
  version,
  versions,
  onDone,
}: {
  setId: number;
  version: SetVersionRow;
  /** Every version of this Set, for the "Compared to" select. */
  versions: SetVersionDetail[];
  onDone: () => void;
}) {
  const save = useSetVersionPrediction();
  const [text, setText] = useState(version.prediction);
  // A version that already states a prediction opens on what it stored, null
  // included: somebody who chose "Nothing" meant it. A version that states none
  // opens on its **parent**, which is what the rest of the app defaults to —
  // seeding from the stored null would make every fresh prediction compare
  // against nothing without anybody choosing that.
  const [compare, setCompare] = useState(
    version.prediction
      ? version.compares_to_version_id
        ? String(version.compares_to_version_id)
        : ""
      : version.parent_version_id
        ? String(version.parent_version_id)
        : "",
  );
  const ids = { text: useId(), compare: useId() };
  const textRef = useRef<HTMLTextAreaElement>(null);

  // The button that opened this editor is gone — it was replaced by the editor
  // — so focus has to land somewhere deliberate or it falls back to the body
  // and a keyboard user starts again from the top of the page. In an effect
  // rather than in the click handler: the field does not exist until this
  // component has rendered.
  useEffect(() => textRef.current?.focus(), []);

  return (
    <form
      data-testid="prediction-editor"
      className="space-y-2 rounded-md border border-border bg-muted/30 p-2"
      onSubmit={async (event) => {
        event.preventDefault();
        const written = await attempt(() =>
          save.mutateAsync({
            setId,
            versionId: version.id,
            body: {
              prediction: text.trim(),
              compares_to_version_id: compare ? Number(compare) : null,
            },
          }),
        );
        if (written) onDone();
      }}
    >
      <div>
        <label htmlFor={ids.text} className="mb-1 block text-muted-foreground text-xs">
          Version prediction
        </label>
        <textarea
          id={ids.text}
          ref={textRef}
          rows={2}
          maxLength={1000}
          className={FIELD}
          placeholder="Compared to v4: less bitter, a shorter shot"
          value={text}
          onChange={(event) => setText(event.target.value)}
        />
      </div>
      <div>
        <label htmlFor={ids.compare} className="mb-1 block text-muted-foreground text-xs">
          Compared to
        </label>
        <select
          id={ids.compare}
          className={cn(FIELD, "h-8 py-0")}
          value={compare}
          onChange={(event) => setCompare(event.target.value)}
        >
          {/* A first version has nothing earlier, and the empty choice says so
              rather than pretending there is a comparison to pick. */}
          {/* An explicit "nothing", which the API takes as a null rather than
              as "not sent": not sent falls back to the parent, which is the
              opposite of what this option says. */}
          <option value="">Nothing — grade it on its own numbers</option>
          {versions
            .map((entry) => entry.version)
            // Older only: the server refuses a comparison against a version
            // that did not exist yet, and offering one would be a 422 waiting
            // to happen.
            .filter((other) => other.version_no < version.version_no)
            .map((other) => (
              <option key={other.id} value={String(other.id)}>
                v{other.version_no}
              </option>
            ))}
        </select>
      </div>
      <div className="flex gap-2">
        <Button type="submit" size="sm" disabled={save.isPending}>
          {text.trim() ? "Save the prediction" : "Remove the prediction"}
        </Button>
        <Button type="button" size="sm" variant="ghost" onClick={onDone}>
          Cancel
        </Button>
      </div>
    </form>
  );
}
