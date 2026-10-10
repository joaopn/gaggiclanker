import { ChevronDown } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { ApiClientError } from "@/api/client";
import type { DuplicatePreview, SetVersionRow } from "@/api/types";
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
import { useBeans, useGrinders } from "@/hooks/useCatalog";
import { useDuplicatePreview, useDuplicateSet } from "@/hooks/useDuplicate";
import { useSingleFlight } from "@/hooks/useSingleFlight";
import { noAnswerSentence } from "@/lib/mutations";
import { beanLabel, recipeLine } from "@/lib/sets";
import { cn } from "@/lib/utils";

const FIELD = cn(
  "h-8 w-full rounded-md border border-input bg-background px-2 text-sm",
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
);

/** What "the same name" means to the server: stripped, case ignored. */
function nameKey(name: string): string {
  return name.trim().toLowerCase();
}

/**
 * "Duplicate as a new Set": the question the duplicate's own preview answers, and the duplicate.
 *
 * One dialog for the whole Set page, opened from a version's button, from a line of the Pinned
 * card, or on arrival from the Profiles page. It opens with the preview, which is the server's
 * word on what the duplicate starts from; the web words nothing a refusal says. A taken profile
 * name shows the server's sentence under the field; a profile the safety policy would change
 * shows the server's sentence and Close, since nothing here would make it pass; any other
 * refusal is shown in place and the form stays. The Set name is the only thing the person must
 * give besides the profile name: the bean, the grinder and the insights are prefilled.
 */
export function DuplicateDialog({
  setId,
  setName,
  version,
  open,
  onOpenChange,
  openerId,
}: {
  setId: number;
  /** The source Set's name, for the title. */
  setName: string;
  version: Pick<SetVersionRow, "id" | "version_label">;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** The button that opened it, which gets focus back when the dialog closes. */
  openerId?: string | null;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className="max-h-[90vh] overflow-y-auto sm:max-w-lg"
        data-testid="duplicate-dialog"
        onCloseAutoFocus={(event) => {
          const opener = openerId ? document.getElementById(openerId) : null;
          if (opener) {
            event.preventDefault();
            opener.focus();
          }
        }}
      >
        <DialogHeader>
          <DialogTitle className="break-words">
            Duplicate "{setName}" from {version.version_label}
          </DialogTitle>
          <DialogDescription className="sr-only">
            Start a new Set from {version.version_label}'s recipe, with no history.
          </DialogDescription>
        </DialogHeader>
        {/* Mounted only while open: every opening asks the preview afresh and starts with a
            clean form, and a refusal from the last opening is not carried over. */}
        {open ? (
          <DuplicateBody
            key={version.id}
            setId={setId}
            version={version}
            onClose={() => onOpenChange(false)}
          />
        ) : null}
      </DialogContent>
    </Dialog>
  );
}

function DuplicateBody({
  setId,
  version,
  onClose,
}: {
  setId: number;
  version: Pick<SetVersionRow, "id" | "version_label">;
  onClose: () => void;
}) {
  const preview = useDuplicatePreview(setId, version.id);
  if (preview.isPending) {
    return <Skeleton className="h-40 w-full" data-testid="duplicate-checking" />;
  }
  if (preview.isError || !preview.data) {
    return (
      <>
        <p className="text-sm" role="alert" data-testid="duplicate-preview-error">
          {preview.error instanceof ApiClientError
            ? preview.error.sentence
            : "This Set could not be checked right now."}
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
    <DuplicateForm
      setId={setId}
      version={version}
      preview={preview.data}
      onClose={onClose}
      onStale={() => void preview.refetch()}
    />
  );
}

/** What the answer to the last request put in front of the person. */
type Answer =
  | { kind: "none" }
  | { kind: "taken"; name: string; sentence: string }
  | { kind: "refused"; sentence: string }
  | { kind: "other"; sentence: string };

function DuplicateForm({
  setId,
  version,
  preview,
  onClose,
  onStale,
}: {
  setId: number;
  version: Pick<SetVersionRow, "id" | "version_label">;
  preview: DuplicatePreview;
  onClose: () => void;
  /** Ask the preview again: what the Set offers to copy has changed under the dialog. */
  onStale: () => void;
}) {
  const duplicate = useDuplicateSet();
  const once = useSingleFlight();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  // Archived bags too: a finished bag is the usual thing to start a new Set from.
  const beans = useBeans(true);
  const grinders = useGrinders();
  const ids = { set: useId(), profile: useId(), bean: useId(), grinder: useId() };
  const [setName, setSetName] = useState(preview.set_name);
  const [profileName, setProfileName] = useState("");
  const [beanId, setBeanId] = useState(String(preview.bean_id));
  const [grinderId, setGrinderId] = useState(
    preview.grinder_id == null ? "" : String(preview.grinder_id),
  );
  // Every insight starts ticked; the ids are the preview's own. Only ids the preview still lists
  // are counted or sent, so an insight that has gone since is dropped when the preview is asked
  // again.
  const [ticked, setTicked] = useState<ReadonlySet<number>>(
    () => new Set(preview.insights.map((insight) => insight.id)),
  );
  const [listOpen, setListOpen] = useState(false);
  // Held in state, so an answer stays on screen while the next request is in flight (the
  // mutation's own error is cleared the moment it starts again).
  const [answer, setAnswer] = useState<Answer>({ kind: "none" });
  const [unreachable, setUnreachable] = useState(false);
  // The page keeps its state across `/sets/:setId`, so whatever the mutation says counts only for
  // the version it was sent for.
  const pending = duplicate.isPending && duplicate.variables?.versionId === version.id;

  if (answer.kind === "refused") {
    return (
      <>
        <p className="text-sm" role="alert" data-testid="duplicate-refused">
          {answer.sentence}
        </p>
        <DialogFooter>
          <Button type="button" variant="outline" onClick={onClose}>
            Close
          </Button>
        </DialogFooter>
      </>
    );
  }

  const taken = answer.kind === "taken" ? answer : null;
  const profileBlank = preview.has_profile && profileName.trim() === "";
  // The same name again would be refused again: the button waits for a different one.
  const profileUnchanged = taken !== null && nameKey(profileName) === nameKey(taken.name);
  // The pickers' options must be there: a select that has not got the prefilled option yet has no
  // option to show, and the browser will not send a form whose required select shows none.
  const loading = beans.isPending || grinders.isPending;
  const blocked =
    loading || setName.trim() === "" || profileBlank || profileUnchanged || beanId === "";

  const submit = () =>
    once((release) => {
      setUnreachable(false);
      duplicate.mutate(
        {
          setId,
          versionId: version.id,
          body: {
            set_name: setName.trim(),
            profile_name: preview.has_profile ? profileName.trim() : null,
            bean_id: Number(beanId),
            grinder_id: grinderId === "" ? null : Number(grinderId),
            insight_ids: preview.insights.filter((i) => ticked.has(i.id)).map((i) => i.id),
          },
        },
        {
          onSuccess: ({ set }) => {
            // Arrived from the Profiles page: take `?duplicate=` out of this history entry first,
            // so Back from the new Set lands on this Set's page and not on this dialog.
            if (params.has("duplicate")) {
              const next = new URLSearchParams(params);
              next.delete("duplicate");
              setParams(next, { replace: true });
            }
            navigate(`/sets/${set.id}`);
          },
          onError: (error) => {
            if (!(error instanceof ApiClientError)) {
              // Nothing the server said, so nothing known about the Set: the form stays for
              // another try, and the Sets list shows the truth.
              setUnreachable(true);
              return;
            }
            if (error.code === "PROFILE_NAME_TAKEN") {
              const name = (error.details as { name?: unknown } | null | undefined)?.name;
              setAnswer({
                kind: "taken",
                name: typeof name === "string" ? name : profileName,
                sentence: error.sentence,
              });
            } else if (error.code === "PROFILE_POLICY_REFUSED") {
              setAnswer({ kind: "refused", sentence: error.sentence });
            } else {
              // A refused insight means the Set's insights changed since the dialog opened: say
              // what the server said, and ask again so the list shows what can be copied now.
              const field = (error.details as { field?: unknown } | null | undefined)?.field;
              if (field === "insight_ids") onStale();
              setAnswer({ kind: "other", sentence: error.sentence });
            }
          },
          onSettled: release,
        },
      );
    });

  return (
    <form
      className="space-y-3"
      onSubmit={(event) => {
        event.preventDefault();
        if (blocked || pending) return;
        submit();
      }}
    >
      <Field id={ids.set} label="Set name">
        <input
          id={ids.set}
          className={FIELD}
          value={setName}
          maxLength={200}
          onChange={(event) => setSetName(event.target.value)}
        />
      </Field>

      {preview.has_profile ? (
        <Field id={ids.profile} label="Profile name">
          <input
            id={ids.profile}
            className={FIELD}
            value={profileName}
            maxLength={200}
            aria-invalid={taken ? true : undefined}
            aria-describedby={taken ? `${ids.profile}-note` : undefined}
            onChange={(event) => setProfileName(event.target.value)}
          />
          {taken ? (
            <p
              id={`${ids.profile}-note`}
              className="mt-1 text-destructive text-xs"
              role="alert"
              data-testid="duplicate-name-taken"
            >
              {taken.sentence}
            </p>
          ) : (
            <p className="mt-1 text-muted-foreground text-xs" data-testid="duplicate-profile-note">
              a new profile, same content as {version.version_label}'s; it goes on the machine
            </p>
          )}
        </Field>
      ) : null}

      <div className="grid gap-3 sm:grid-cols-2">
        <Field id={ids.bean} label="Bean">
          <select
            id={ids.bean}
            required
            className={FIELD}
            value={beanId}
            onChange={(event) => setBeanId(event.target.value)}
          >
            {(beans.data?.items ?? []).map((bean) => (
              <option key={bean.id} value={String(bean.id)}>
                {beanLabel(bean)}
                {bean.archived ? " (archived)" : ""}
              </option>
            ))}
          </select>
        </Field>
        <Field id={ids.grinder} label="Grinder">
          <select
            id={ids.grinder}
            className={FIELD}
            value={grinderId}
            onChange={(event) => setGrinderId(event.target.value)}
          >
            <option value="">Not recorded</option>
            {(grinders.data?.items ?? []).map((grinder) => (
              <option key={grinder.id} value={String(grinder.id)}>
                {grinder.name}
              </option>
            ))}
          </select>
        </Field>
      </div>

      <p className="text-sm" data-testid="duplicate-recipe">
        <span className="text-muted-foreground text-xs">Recipe </span>
        {recipeLine(preview.recipe)}{" "}
        <span className="text-muted-foreground text-xs">
          ({version.version_label}'s; change it later)
        </span>
      </p>

      {preview.insights.length > 0 ? (
        <InsightsChoice
          insights={preview.insights}
          ticked={ticked}
          onChange={setTicked}
          open={listOpen}
          onToggle={() => setListOpen((shown) => !shown)}
        />
      ) : null}

      {answer.kind === "other" ? (
        <p className="text-destructive text-sm" role="alert" data-testid="duplicate-other">
          {answer.sentence}
        </p>
      ) : null}
      {unreachable ? (
        <p className="text-destructive text-sm" role="alert" data-testid="duplicate-unreachable">
          {noAnswerSentence("the Sets list")}
        </p>
      ) : null}
      <DialogFooter>
        <Button type="button" variant="ghost" onClick={onClose}>
          Cancel
        </Button>
        <Button type="submit" disabled={pending || blocked}>
          Duplicate
        </Button>
      </DialogFooter>
    </form>
  );
}

/**
 * "Copy insights (n of m confirmed)": one box for all of them, and the list behind a toggle.
 * Unticking the box unticks every insight; ticking it ticks every one; the box is half-ticked
 * while only some are.
 */
function InsightsChoice({
  insights,
  ticked,
  onChange,
  open,
  onToggle,
}: {
  insights: DuplicatePreview["insights"];
  ticked: ReadonlySet<number>;
  onChange: (next: ReadonlySet<number>) => void;
  open: boolean;
  onToggle: () => void;
}) {
  const boxId = useId();
  const listId = useId();
  const master = useRef<HTMLInputElement>(null);
  const count = insights.filter((insight) => ticked.has(insight.id)).length;
  const all = count === insights.length;
  useEffect(() => {
    if (master.current) master.current.indeterminate = count > 0 && !all;
  }, [count, all]);
  return (
    <div className="space-y-2 rounded-md border border-border p-2" data-testid="duplicate-insights">
      <div className="flex items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2">
          <input
            ref={master}
            id={boxId}
            type="checkbox"
            className="size-4 shrink-0"
            checked={all}
            onChange={() =>
              onChange(all ? new Set() : new Set(insights.map((insight) => insight.id)))
            }
          />
          <label htmlFor={boxId} className="min-w-0 text-sm">
            Copy insights ({count} of {insights.length} chosen)
          </label>
        </div>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className="size-7 shrink-0"
          aria-expanded={open}
          aria-controls={listId}
          aria-label={open ? "Hide the insights" : "Show the insights"}
          onClick={onToggle}
        >
          <ChevronDown
            className={cn("size-4 transition-transform", open && "rotate-180")}
            aria-hidden="true"
          />
        </Button>
      </div>
      {open ? (
        <ul id={listId} className="space-y-1.5 pl-6" data-testid="duplicate-insight-list">
          {insights.map((insight) => (
            <li key={insight.id}>
              <label className="flex items-start gap-2 text-sm">
                <input
                  type="checkbox"
                  className="mt-0.5 size-4 shrink-0"
                  checked={ticked.has(insight.id)}
                  onChange={() => {
                    const next = new Set(ticked);
                    if (!next.delete(insight.id)) next.add(insight.id);
                    onChange(next);
                  }}
                />
                <span className="min-w-0 break-words">{insight.text}</span>
              </label>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function Field({ id, label, children }: { id: string; label: string; children: React.ReactNode }) {
  return (
    <div>
      <label htmlFor={id} className="mb-1 block text-muted-foreground text-xs">
        {label}
      </label>
      {children}
    </div>
  );
}
