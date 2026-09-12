import { useEffect, useState } from "react";
import type { PromptSummary } from "@/api/types";
import { SectionCard } from "@/components/layout/SectionCard";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { usePrompt, usePrompts, useResetPrompt, useSavePrompt } from "@/hooks/usePrompts";

/**
 * The prompt editor.
 *
 * A prompt is data, not code (`gaggiclanker/llm/prompts.py`), so this is where
 * the wording of an analysis is changed - and the change reaches the next call
 * with no restart. Two things the UI has to be honest about:
 *
 * - **"Edited" is a real state.** An edited prompt no longer tracks the one
 *   that ships, so an upgrade will not improve it. The badge says so, and reset
 *   puts it back.
 * - **The server validates, not the browser.** Parsing YAML here to preview the
 *   error would mean two validators disagreeing about which documents are legal,
 *   which is worse than one round trip.
 */
export function PromptsSection() {
  const prompts = usePrompts();
  const [selected, setSelected] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const prompt = usePrompt(selected);
  const save = useSavePrompt();
  const reset = useResetPrompt();

  const list: PromptSummary[] = prompts.data?.prompts ?? [];

  // Pick the first prompt once the list arrives, so the editor is never an
  // empty box with a dropdown nobody noticed.
  useEffect(() => {
    if (selected === null && list.length > 0) setSelected(list[0].name);
  }, [list, selected]);

  // Re-seed the box whenever the stored text changes - a different prompt, a
  // save, a reset. Keyed on the content itself rather than on a render count,
  // so a failed save leaves what the user typed alone.
  const stored = prompt.data?.content ?? "";
  useEffect(() => {
    setDraft(stored);
  }, [stored]);

  const current = list.find((entry) => entry.name === selected);
  const dirty = draft !== stored;

  return (
    <SectionCard
      title="Prompts"
      description="The text every LLM call renders. Edits take effect on the next call - no restart."
      contentClassName="space-y-4"
    >
      {prompts.isPending ? (
        <Skeleton className="h-48 w-full" data-testid="prompts-skeleton" />
      ) : (
        <>
          <div className="space-y-1.5">
            <Label htmlFor="prompt-picker">Prompt</Label>
            <Select value={selected ?? ""} onValueChange={setSelected}>
              <SelectTrigger id="prompt-picker" aria-label="Prompt">
                <SelectValue placeholder="Choose a prompt" />
              </SelectTrigger>
              <SelectContent>
                {list.map((entry) => (
                  <SelectItem key={entry.name} value={entry.name}>
                    {entry.name}
                    {entry.edited ? " (edited)" : ""}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            {current?.edited ? (
              <Badge variant="secondary" className="font-normal text-[10px]">
                edited - no longer tracks the shipped version
              </Badge>
            ) : (
              <Badge variant="outline" className="font-normal text-[10px]">
                as shipped
              </Badge>
            )}
            {current?.valid === false ? (
              <Badge variant="destructive" className="font-normal text-[10px]">
                does not parse
              </Badge>
            ) : null}
            {(current?.variables ?? []).map((variable) => (
              <Badge
                key={String(variable.name)}
                variant="outline"
                className="font-mono font-normal text-[10px]"
              >
                {`{{${String(variable.name)}}}`}
              </Badge>
            ))}
          </div>

          <Textarea
            aria-label="Prompt content"
            className="h-72 font-mono text-xs"
            spellCheck={false}
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            disabled={prompt.isPending || !selected}
          />

          <div className="flex flex-wrap items-center gap-3">
            <Button
              type="button"
              onClick={() => selected && save.mutate({ name: selected, content: draft })}
              disabled={!dirty || save.isPending || !selected}
            >
              {save.isPending ? "Saving..." : "Save prompt"}
            </Button>
            <Button
              type="button"
              variant="outline"
              onClick={() => selected && reset.mutate(selected)}
              disabled={!current?.edited || reset.isPending}
            >
              {reset.isPending ? "Resetting..." : "Reset to default"}
            </Button>
            <p className="text-muted-foreground text-xs">
              Saving validates the YAML and every <code>{"{{> fragment}}"}</code> it names; a
              document that would not render is refused rather than stored.
            </p>
          </div>
        </>
      )}
    </SectionCard>
  );
}
