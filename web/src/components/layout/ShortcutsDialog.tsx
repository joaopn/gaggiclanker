import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { NAV_LINKS } from "@/lib/navigation";

/** Rendered from the nav table, so it cannot drift from the real bindings. */
export function ShortcutsDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Keyboard shortcuts</DialogTitle>
          <DialogDescription>
            Press the keys in sequence. They are ignored while you are typing in a field.
          </DialogDescription>
        </DialogHeader>
        <dl className="grid grid-cols-[auto_1fr] items-center gap-x-4 gap-y-2 text-sm">
          {NAV_LINKS.map((link) => (
            <div key={link.to} className="contents">
              <dt className="font-mono text-muted-foreground text-xs">{link.shortcutLabel}</dt>
              <dd>Go to {link.label}</dd>
            </div>
          ))}
          <div className="contents">
            <dt className="font-mono text-muted-foreground text-xs">?</dt>
            <dd>This dialog</dd>
          </div>
        </dl>
      </DialogContent>
    </Dialog>
  );
}
