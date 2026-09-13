import { Columns3 } from "lucide-react";
import { useId, useState } from "react";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { SHOT_COLUMNS, type ShotColumnId } from "@/lib/shotColumns";

/**
 * Which columns the table draws.
 *
 * A checkbox list in a panel rather than a settings page: it is a view
 * preference, it is changed while looking at the thing it changes, and the
 * answer is visible the moment it is clicked. The last visible column cannot be
 * turned off — a table with no columns is not a preference, it is a broken
 * page — so its checkbox is disabled rather than silently ignored.
 */
export function ColumnChooser({
  visible,
  onChange,
}: {
  visible: ShotColumnId[];
  onChange: (next: ShotColumnId[]) => void;
}) {
  const [open, setOpen] = useState(false);
  const prefix = useId();
  const shown = new Set(visible);

  function toggle(id: ShotColumnId) {
    const next = shown.has(id)
      ? visible.filter((value) => value !== id)
      : // Rebuilt from the canonical order rather than appended: the order of
        // the columns is fixed, so a column turned off and on again has to come
        // back where it was and not at the end.
        SHOT_COLUMNS.map((column) => column.id).filter((value) => shown.has(value) || value === id);
    if (next.length === 0) return;
    onChange(next);
  }

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button variant="outline" size="sm" data-testid="columns-button">
          <Columns3 className="size-3.5" aria-hidden="true" />
          Columns
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-52" data-testid="columns-menu" aria-label="Columns">
        <fieldset className="space-y-1.5">
          <legend className="mb-2 text-muted-foreground text-xs">Columns</legend>
          {SHOT_COLUMNS.map((column) => {
            const id = `${prefix}-${column.id}`;
            const last = shown.has(column.id) && visible.length === 1;
            return (
              <div key={column.id} className="flex items-center gap-2">
                <input
                  id={id}
                  type="checkbox"
                  className="size-3.5 accent-primary"
                  checked={shown.has(column.id)}
                  disabled={last}
                  onChange={() => toggle(column.id)}
                />
                <label htmlFor={id} className="text-sm">
                  {column.label}
                </label>
              </div>
            );
          })}
        </fieldset>
      </PopoverContent>
    </Popover>
  );
}
