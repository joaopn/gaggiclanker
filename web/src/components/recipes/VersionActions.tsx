import { Copy, Pin, Undo2 } from "lucide-react";
import { type ReactNode, useId, useState } from "react";
import type { RecipeVersionDetail } from "@/api/types";
import { duplicateButtonId } from "@/components/recipes/duplicateIds";
import { pinButtonId } from "@/components/recipes/PinDialog";
import { RollbackPanel, RollbackTrigger } from "@/components/recipes/RollbackButton";
import { Button } from "@/components/ui/button";

/**
 * The one row of buttons a version of the log offers: going back to it, pinning it, duplicating it
 * as a new Recipe, and whatever a later feature adds for every version. A button is here only while it applies; the row is
 * rendered for every version and takes no room when it holds none. The confirmation that "Go
 * back" opens sits below the row, never inside it, so opening it does not move the buttons.
 */
export function VersionActions({
  recipeId,
  entry,
  current,
  canPin,
  onPin,
  onDuplicate,
  children,
}: {
  recipeId: number;
  entry: RecipeVersionDetail;
  /** The version the Recipe is on: there is nowhere to go back to from it. */
  current: boolean;
  canPin: boolean;
  onPin: () => void;
  /** Open the Duplicate dialog for this version; absent where a version cannot be duplicated. */
  onDuplicate?: () => void;
  /** Further buttons that belong to every version. */
  children?: ReactNode;
}) {
  const [rollbackOpen, setRollbackOpen] = useState(false);
  const panelId = useId();
  const { version } = entry;
  const hasButtons = !current || canPin || onDuplicate != null || children != null;
  return (
    <div data-testid="version-actions" className="empty:hidden">
      {hasButtons ? (
        <div className="flex flex-wrap gap-2">
          {current ? null : (
            <RollbackTrigger
              open={rollbackOpen}
              panelId={panelId}
              onToggle={() => setRollbackOpen((shown) => !shown)}
              label="Go back to this version"
              icon={<Undo2 className="size-3.5" aria-hidden="true" />}
            />
          )}
          {canPin ? (
            <Button
              id={pinButtonId(version.id)}
              type="button"
              size="sm"
              variant="outline"
              aria-label={`Pin to the machine: ${version.version_label}`}
              onClick={onPin}
            >
              <Pin className="size-3.5" aria-hidden="true" />
              Pin to the machine
            </Button>
          ) : null}
          {onDuplicate ? (
            <Button
              id={duplicateButtonId(version.id)}
              type="button"
              size="sm"
              variant="outline"
              aria-label={`Duplicate Recipe: ${version.version_label}`}
              onClick={onDuplicate}
            >
              <Copy className="size-3.5" aria-hidden="true" />
              Duplicate Recipe
            </Button>
          ) : null}
          {children}
        </div>
      ) : null}
      {current ? null : (
        <RollbackPanel
          recipeId={recipeId}
          versionId={version.id}
          versionLabel={version.version_label}
          open={rollbackOpen}
          panelId={panelId}
          onClose={() => setRollbackOpen(false)}
        />
      )}
    </div>
  );
}
