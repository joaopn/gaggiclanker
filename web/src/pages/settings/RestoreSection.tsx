import { useRef, useState } from "react";
import { ApiClientError, applyRestore, cancelRestore, checkRestore } from "@/api/client";
import type { RestoreCheckData } from "@/api/types";
import { Button } from "@/components/ui/button";
import { RESTART_GIVE_UP_MS, RestartOverlay } from "@/pages/settings/RestartOverlay";

// The restart overlay is shared with the reset; it is re-exported so this section stays the
// one place its restore wording and tests are found.
export { RESTART_GIVE_UP_MS, RestartOverlay };

/** What a refusal means to the person, by the server's stable code. Nothing has changed. */
export function refusalText(error: unknown): string {
  const code = error instanceof ApiClientError ? error.code : undefined;
  switch (code) {
    case "RESTORE_NOT_A_DATABASE":
      return "This file can't be restored: it is not a gaggiclanker database.";
    case "RESTORE_DAMAGED":
      return "This file can't be restored: it is damaged (its integrity check failed).";
    case "RESTORE_NEWER_VERSION":
      return "This file can't be restored: it was written by a newer version of gaggiclanker. Update the app, then restore it.";
    case "RESTORE_MIGRATION_DIFFERS":
      return "This file can't be restored: a migration in it differs from this version's.";
    case "PAYLOAD_TOO_LARGE":
      return "This file can't be restored: it is larger than the 1 GB limit.";
    case "INSUFFICIENT_STORAGE":
      return "This file can't be restored: there is not enough free disk space on the server.";
    case "RESTORE_BUSY":
      return "A sync, a chat answer or a review is running. Try again when it finishes.";
    case "RESTORE_PENDING":
      return "A restore is already under way.";
    case "RESET_PENDING":
      return "A reset is under way; the app is about to restart.";
    default:
      return error instanceof Error
        ? `This file can't be restored: ${error.message}`
        : "This file can't be restored.";
  }
}

function formatTaken(createdAt: string): string {
  const date = new Date(createdAt);
  if (Number.isNaN(date.getTime())) return createdAt;
  return date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

function keysCell(check: RestoreCheckData): string {
  if (check.keys_in_file) return "included";
  return check.keys_here ? "not included, yours are kept" : "not included";
}

type Phase =
  | { kind: "idle" }
  | { kind: "checking"; name: string }
  | { kind: "preview"; check: RestoreCheckData }
  | { kind: "refused"; message: string }
  | { kind: "restarting" };

/**
 * Restore from an uploaded file: choose, look, confirm, restart.
 *
 * One meaning per state. Choosing a file only uploads and checks it (the server changes
 * nothing); the preview says what is in the file and what will happen; the confirm is inline
 * (no dialog, which jsdom cannot open here); a refusal says why in the same place.
 */
export function RestoreSection() {
  const [phase, setPhase] = useState<Phase>({ kind: "idle" });
  const [busy, setBusy] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const applying = useRef(false);

  async function choose(file: File) {
    setPhase({ kind: "checking", name: file.name });
    try {
      setPhase({ kind: "preview", check: await checkRestore(file) });
    } catch (error) {
      setPhase({ kind: "refused", message: refusalText(error) });
    }
  }

  async function cancel(token: string) {
    setBusy(true);
    try {
      await cancelRestore(token);
    } catch {
      // The file is deleted at the next start anyway; the person has still said no.
    } finally {
      setBusy(false);
      setPhase({ kind: "idle" });
    }
  }

  async function confirm(token: string) {
    // A ref, not the button's disabled state: a double click lands before React re-renders.
    if (applying.current) return;
    applying.current = true;
    setBusy(true);
    try {
      await applyRestore(token);
      setPhase({ kind: "restarting" });
    } catch (error) {
      applying.current = false;
      setPhase({ kind: "refused", message: refusalText(error) });
    } finally {
      setBusy(false);
    }
  }

  const picker = (
    <input
      ref={input}
      type="file"
      className="hidden"
      data-testid="restore-file"
      aria-label="Restore file"
      onChange={(event) => {
        const file = event.target.files?.[0];
        event.target.value = "";
        if (file) void choose(file);
      }}
    />
  );

  return (
    <div className="space-y-2">
      <h3 className="font-medium text-sm">Restore</h3>
      {picker}
      {phase.kind === "idle" && (
        <>
          <p className="text-muted-foreground text-sm">
            Replaces everything in the app with what the file holds.
          </p>
          <Button variant="outline" type="button" onClick={() => input.current?.click()}>
            Restore from file…
          </Button>
        </>
      )}
      {phase.kind === "checking" && <p className="text-sm">Checking {phase.name}…</p>}
      {phase.kind === "refused" && (
        <>
          <p className="text-destructive text-sm" role="alert">
            {phase.message}
          </p>
          <Button variant="outline" type="button" onClick={() => input.current?.click()}>
            Choose another file
          </Button>
        </>
      )}
      {phase.kind === "preview" && (
        <Preview
          check={phase.check}
          busy={busy}
          onConfirm={() => void confirm(phase.check.token)}
          onCancel={() => void cancel(phase.check.token)}
        />
      )}
      {phase.kind === "restarting" && <RestartOverlay />}
    </div>
  );
}

function Preview({
  check,
  busy,
  onConfirm,
  onCancel,
}: {
  check: RestoreCheckData;
  busy: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const manifest = check.manifest;
  return (
    <div className="space-y-3 text-sm">
      <p className="font-medium">Restore {check.filename}?</p>
      <p className="text-muted-foreground">
        {manifest
          ? `Taken ${formatTaken(manifest.created_at)} by gaggiclanker ${manifest.app_version}`
          : "A plain copy of a gaggiclanker database (no backup details)"}
      </p>
      <table className="text-sm">
        <thead>
          <tr className="text-left text-muted-foreground text-xs">
            <th className="pr-6 font-normal" />
            <th className="pr-6 font-normal">In the file</th>
            <th className="font-normal">Now</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td className="pr-6">Shots</td>
            <td className="pr-6">{check.in_file.shots}</td>
            <td>{check.now.shots}</td>
          </tr>
          <tr>
            <td className="pr-6">Sets</td>
            <td className="pr-6">{check.in_file.sets}</td>
            <td>{check.now.sets}</td>
          </tr>
          <tr>
            <td className="pr-6">Beans</td>
            <td className="pr-6">{check.in_file.beans}</td>
            <td>{check.now.beans}</td>
          </tr>
          <tr>
            <td className="pr-6">API keys and tokens</td>
            <td className="pr-6">{keysCell(check)}</td>
            <td>{check.keys_here ? "set" : "none"}</td>
          </tr>
        </tbody>
      </table>
      <div>
        <p>What happens:</p>
        <ul className="list-disc pl-5">
          <li>Everything in the app is replaced by the file.</li>
          <li>What the app holds now is gone, unless you have downloaded a backup of it.</li>
          <li>Writes to the machine are switched off; switch them on again when you are ready.</li>
          <li>Everyone is signed out.</li>
          <li>The app restarts, which takes a few seconds.</li>
        </ul>
      </div>
      <div className="flex gap-2">
        <Button type="button" variant="destructive" disabled={busy} onClick={onConfirm}>
          Restore and restart
        </Button>
        <Button type="button" variant="outline" disabled={busy} onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </div>
  );
}
