import { AlertTriangle } from "lucide-react";
import { useEffect, useState } from "react";
import type { PromptSummary } from "@/api/types";
import { EmptyState } from "@/components/layout/EmptyState";
import { PageHeader } from "@/components/layout/PageHeader";
import { SectionCard } from "@/components/layout/SectionCard";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { usePrompt, usePrompts, useResetPrompt, useSavePrompt } from "@/hooks/usePrompts";
import { useQueryErrorToast } from "@/hooks/useQueryErrorToast";
import type { SettingsPageInfo } from "@/lib/settingsPages";
import { useOpenGroups } from "@/pages/settings/useOpenGroups";

/**
 * The prompt editor: one card per prompt.
 *
 * A prompt is data, not code (`gaggiclanker/llm/prompts.py`), so this is where
 * the wording of an analysis is changed - and the change reaches the next call
 * with no restart. Two things the UI has to be honest about:
 *
 * - **"Edited" is a real state.** An edited prompt no longer tracks the one
 *   that ships, so an upgrade will not improve it. The badge on its card says
 *   so, closed or open, and reset puts it back.
 * - **The server validates, not the browser.** Parsing YAML here to preview the
 *   error would mean two validators disagreeing about which documents are legal,
 *   which is worse than one round trip.
 *
 * A card's editor mounts the first time the card opens and then stays: the
 * prompt's text is a request of its own, and closing a card must not throw
 * away an unsaved draft.
 */
export function PromptsPage({ page }: { page: SettingsPageInfo }) {
  const prompts = usePrompts();
  const { isOpen, setGroupOpen } = useOpenGroups();
  const [mounted, setMounted] = useState<ReadonlySet<string>>(new Set());

  useQueryErrorToast(prompts.error, "Could not load the prompts");

  const list: PromptSummary[] = prompts.data?.prompts ?? [];

  // A card opened by a link (`#analysis`) mounts its editor like a clicked one.
  useEffect(() => {
    const linked = list.filter((prompt) => isOpen(prompt.name) && !mounted.has(prompt.name));
    if (linked.length > 0)
      setMounted((previous) => new Set([...previous, ...linked.map((p) => p.name)]));
  }, [list, isOpen, mounted]);

  const toggle = (name: string, open: boolean) => {
    setGroupOpen(name, open);
    if (open) setMounted((previous) => new Set(previous).add(name));
  };

  return (
    <div className="space-y-6">
      <PageHeader title={page.label} subtitle={page.description} />

      {prompts.isPending ? (
        <div className="space-y-3" data-testid="prompts-skeleton">
          <Skeleton className="h-20 w-full" />
          <Skeleton className="h-20 w-full" />
        </div>
      ) : prompts.isError ? (
        <EmptyState
          icon={AlertTriangle}
          title="Could not load the prompts"
          description={prompts.error.message}
          action={
            <Button variant="outline" onClick={() => void prompts.refetch()}>
              Try again
            </Button>
          }
        />
      ) : (
        list.map((prompt) => (
          <SectionCard
            key={prompt.name}
            id={prompt.name}
            collapsible
            open={isOpen(prompt.name)}
            onOpenChange={(open) => toggle(prompt.name, open)}
            title={<span className="font-mono">{prompt.name}</span>}
            description={firstParagraph(prompt.description)}
            actions={<PromptBadges prompt={prompt} />}
            contentClassName="space-y-4"
          >
            {mounted.has(prompt.name) ? <PromptEditor summary={prompt} /> : null}
          </SectionCard>
        ))
      )}
    </div>
  );
}

/** A prompt's description is a docstring; its first paragraph is the summary. */
function firstParagraph(text: string): string {
  return (text.split(/\n\s*\n/)[0] ?? "").replace(/\s+/g, " ").trim();
}

function PromptBadges({ prompt }: { prompt: PromptSummary }) {
  return (
    <>
      {prompt.fragment ? (
        <Badge variant="outline" className="font-normal text-[10px]">
          fragment
        </Badge>
      ) : null}
      {prompt.edited ? (
        <Badge variant="secondary" className="font-normal text-[10px]">
          edited - no longer tracks the shipped version
        </Badge>
      ) : (
        <Badge variant="outline" className="font-normal text-[10px]">
          as shipped
        </Badge>
      )}
      {prompt.valid === false ? (
        <Badge variant="destructive" className="font-normal text-[10px]">
          does not parse
        </Badge>
      ) : null}
    </>
  );
}

function PromptEditor({ summary }: { summary: PromptSummary }) {
  const prompt = usePrompt(summary.name);
  const save = useSavePrompt();
  const reset = useResetPrompt();
  const [draft, setDraft] = useState("");

  // Re-seed the box whenever the stored text changes - the first load, a save,
  // a reset. Keyed on the content itself rather than on a render count, so a
  // failed save leaves what the user typed alone.
  const stored = prompt.data?.content ?? "";
  useEffect(() => {
    setDraft(stored);
  }, [stored]);

  const dirty = draft !== stored;

  if (prompt.isPending) return <Skeleton className="h-48 w-full" />;

  return (
    <>
      {summary.variables.length > 0 ? (
        <div className="flex flex-wrap items-center gap-2">
          {summary.variables.map((variable) => (
            <Badge
              key={String(variable.name)}
              variant="outline"
              className="font-mono font-normal text-[10px]"
            >
              {`{{${String(variable.name)}}}`}
            </Badge>
          ))}
        </div>
      ) : null}

      <Textarea
        aria-label={`Content of ${summary.name}`}
        className="h-72 font-mono text-xs"
        spellCheck={false}
        value={draft}
        onChange={(event) => setDraft(event.target.value)}
      />

      <div className="flex flex-wrap items-center gap-3">
        <Button
          type="button"
          onClick={() => save.mutate({ name: summary.name, content: draft })}
          disabled={!dirty || save.isPending}
        >
          {save.isPending ? "Saving..." : "Save prompt"}
        </Button>
        <Button
          type="button"
          variant="outline"
          onClick={() => reset.mutate(summary.name)}
          disabled={!summary.edited || reset.isPending}
        >
          {reset.isPending ? "Resetting..." : "Reset to default"}
        </Button>
        <p className="text-muted-foreground text-xs">
          Saving validates the YAML and every <code>{"{{> fragment}}"}</code> it names; a document
          that would not render is refused rather than stored.
        </p>
      </div>
    </>
  );
}
