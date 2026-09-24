import { useQueryClient } from "@tanstack/react-query";
import { Download, Loader2, RotateCcw } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useClaudeCli, useInstallClaudeCli, useRemoveClaudeCli } from "@/hooks/useLlm";
import { queryKeys } from "@/lib/queryKeys";

/** Mirrors `valid_target` in `llm/claude_cli.py`: the server refuses anything else. */
const EXACT_VERSION = /^\d+\.\d+\.\d+(-[A-Za-z0-9.]+)?$/;

/**
 * Updating the Claude Code CLI without rebuilding the image, as cvclanker's
 * panel does. The server downloads the release from npm, checks it against the
 * registry's sha512, runs it once and only then switches to it, so a failed
 * install leaves the binary in use untouched.
 */
export function ClaudeCliUpdater() {
  const cli = useClaudeCli();
  const install = useInstallClaudeCli();
  const remove = useRemoveClaudeCli();
  const queryClient = useQueryClient();
  const [exact, setExact] = useState("");

  // The install ends on the server, seen by the poll: that is the moment to
  // say how it went and to refresh the version badge above.
  const job = cli.data?.job;
  const previous = useRef(job?.state);
  useEffect(() => {
    const was = previous.current;
    previous.current = job?.state;
    if (was !== "running" || !job || job.state === "running") return;
    if (job.state === "done") toast.success(job.message);
    else if (job.state === "failed") toast.error(`Claude Code install failed: ${job.message}`);
    void queryClient.invalidateQueries({ queryKey: queryKeys.llm.status() });
  }, [job, queryClient]);

  const data = cli.data;
  if (!data) {
    return cli.isError ? (
      <p className="text-destructive text-xs">Could not read the Claude Code updater.</p>
    ) : null;
  }

  const running = data.job.state === "running" || install.isPending;
  const inUse = data.overridden
    ? null
    : (data.managed.version ?? data.bundled.version ?? "unknown");
  const busy = running || remove.isPending;

  return (
    <div className="space-y-3" data-testid="claude-cli-updater">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className="text-muted-foreground">Image:</span>
        <Badge variant="outline" className="font-normal text-[10px]">
          {data.bundled.version ?? "none"}
        </Badge>
        <span className="text-muted-foreground">Installed here:</span>
        <Badge
          variant="outline"
          className="font-normal text-[10px]"
          data-testid="claude-cli-managed"
        >
          {data.managed.version ?? "none"}
        </Badge>
        {inUse ? (
          <>
            <span className="text-muted-foreground">In use:</span>
            <Badge variant="secondary" className="font-normal text-[10px]">
              {inUse}
            </Badge>
          </>
        ) : null}
      </div>

      {data.overridden ? (
        <p className="text-muted-foreground text-xs" data-testid="claude-cli-overridden">
          The binary setting names {data.active_binary}, so that runs, whatever is installed here.
          Set it back to <code>claude</code> to use an installed release.
        </p>
      ) : null}
      {data.platform_package === null ? (
        <p className="text-muted-foreground text-xs">
          Claude Code publishes no binary for this platform; updates are unavailable.
        </p>
      ) : null}

      <div className="flex flex-wrap items-center gap-2">
        {(["stable", "latest"] as const).map((channel) => {
          const version = data.channels[channel];
          const current = version !== undefined && version === inUse;
          return (
            <Button
              key={channel}
              type="button"
              variant="outline"
              size="sm"
              disabled={busy || data.platform_package === null}
              onClick={() => install.mutate(channel)}
            >
              <Download className="size-4" />
              {`Install ${channel}${version ? ` (${version})` : ""}`}
              {current ? " - in use" : ""}
            </Button>
          );
        })}
        <Input
          aria-label="Exact Claude Code version"
          placeholder="2.1.267"
          value={exact}
          onChange={(event) => setExact(event.target.value.trim())}
          className="h-8 w-28"
          disabled={busy}
        />
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={busy || !EXACT_VERSION.test(exact) || data.platform_package === null}
          onClick={() => install.mutate(exact)}
        >
          Install version
        </Button>
        {data.managed.version ? (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            disabled={busy}
            onClick={() => remove.mutate()}
          >
            <RotateCcw className="size-4" />
            Use the image's version
          </Button>
        ) : null}
      </div>

      {running ? (
        <p className="flex items-center gap-2 text-xs" data-testid="claude-cli-job">
          <Loader2 className="size-3 animate-spin" />
          Installing {data.job.version || data.job.target}...
        </p>
      ) : data.job.state === "failed" ? (
        <p className="text-destructive text-xs" data-testid="claude-cli-job">
          {data.job.message}
        </p>
      ) : null}

      <p className="text-muted-foreground text-xs">
        Downloads the release from npm into the data directory, checks it against the registry's
        sha512 and runs it once before switching. It survives restarts; once a newer image carries
        that release or a later one, the image's binary takes over again. Nothing updates on its
        own: the provider parses this CLI's output, so a new release is a choice.
      </p>
    </div>
  );
}
