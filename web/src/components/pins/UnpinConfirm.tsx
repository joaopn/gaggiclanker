import { ConfirmStrip } from "@/components/sync/ConfirmStrip";
import { useUnpin } from "@/hooks/usePins";
import { useSingleFlight } from "@/hooks/useSingleFlight";
import { selectedSuccessorSentence } from "@/lib/board";

/**
 * The question under "Unpin", on the Recipe page's card and on the pinned row of the Profiles page.
 *
 * One meaning: the next sync takes the copy off the machine, and the shots brewed on it stay
 * where they are filed. When the copy is the profile the machine has selected, the sync selects
 * another first, in the words the switch-off confirmation uses (`selectedSuccessorSentence`).
 * A second click while the request is in flight is ignored.
 */
export function UnpinConfirm({
  pinId,
  name,
  versionLabel,
  shotCount,
  selected,
  onCancel,
  onDone,
}: {
  pinId: number;
  /** The copy's name on the machine. */
  name: string;
  /** The Recipe version the shots stay filed under, "v1.2". */
  versionLabel: string;
  shotCount: number;
  /** The row's `machine.selected`. */
  selected: boolean;
  onCancel: () => void;
  onDone: () => void;
}) {
  const unpin = useUnpin();
  const once = useSingleFlight();
  return (
    <ConfirmStrip
      title={`Unpin ${versionLabel}?`}
      confirmLabel="Unpin"
      confirmVariant="default"
      testId="unpin-confirm"
      focusOnOpen
      onCancel={onCancel}
      onConfirm={() =>
        once((release) =>
          unpin.mutate(
            { pinId },
            {
              onSuccess: onDone,
              onSettled: release,
            },
          ),
        )
      }
    >
      <div className="space-y-2">
        <p data-testid="unpin-words">
          The next sync takes "{name}" off the machine.
          {shotCount > 0
            ? ` Its ${shotCount} ${shotCount === 1 ? "shot stays" : "shots stay"} filed under ${versionLabel}.`
            : ""}
        </p>
        {selected ? <p data-testid="unpin-selected">{selectedSuccessorSentence()}</p> : null}
      </div>
    </ConfirmStrip>
  );
}
