import { RefreshCw } from "lucide-react";
import { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import type { KnowledgeRule } from "@/api/types";
import { DocsTab } from "@/components/knowledge/DocsTab";
import { InsightsTab } from "@/components/knowledge/InsightsTab";
import { PageHeader } from "@/components/layout/PageHeader";
import { SectionCard } from "@/components/layout/SectionCard";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  useKnowledgeRules,
  usePatchKnowledgeRule,
  useReloadKnowledgeRules,
} from "@/hooks/useKnowledge";
import { useQueryErrorToast } from "@/hooks/useQueryErrorToast";
import { attempt } from "@/lib/mutations";
import { cn } from "@/lib/utils";

/** The three tiers, as tabs. `?tab=` so every one of them is linkable. */
const TABS = ["rules", "docs", "insights"] as const;
type Tab = (typeof TABS)[number];

/**
 * The whole knowledge base, one tab per tier.
 *
 * This page exists because the analyzer is asked to name what it used: the value
 * of those lists is only realised if the reader can follow a citation here, read
 * what it actually says, and turn it off when it turns out to mislead. So every
 * citation deep-links — `?rule=<key>` to a rule, `?tab=docs&doc=<slug>&chunk=<heading path>`
 * to the passage an excerpt came from — and the parameters are what select the
 * tab, so a link never lands on the wrong one.
 *
 * The tiers get different controls because they are owned by different people.
 * Rules and documents are shipped in files: they are edited and reset, never
 * deleted, and an edit survives every upgrade. Insights are this box's own, so
 * they are created, confirmed and really deleted.
 */
export function KnowledgePage() {
  const [params, setParams] = useSearchParams();
  const highlighted = params.get("rule");
  const doc = params.get("doc");
  const chunk = params.get("chunk");
  const requested = params.get("tab");
  // A `?doc=` link that forgot its `?tab=` still lands on the Docs tab: the
  // parameter that says what to show outranks the one that says where.
  const tab: Tab = doc ? "docs" : TABS.includes(requested as Tab) ? (requested as Tab) : "rules";

  const setTab = (next: string) => {
    const updated = new URLSearchParams(params);
    updated.set("tab", next);
    if (next !== "docs") {
      updated.delete("doc");
      updated.delete("chunk");
    }
    setParams(updated, { replace: true });
  };

  const openDoc = (slug: string | null, chunk?: string) => {
    const updated = new URLSearchParams(params);
    updated.set("tab", "docs");
    if (slug) {
      updated.set("doc", slug);
      // A search hit names a passage, not a file. Carried in the URL so the
      // link is shareable and a reload lands in the same place.
      if (chunk) updated.set("chunk", chunk);
      else updated.delete("chunk");
    } else {
      updated.delete("doc");
      updated.delete("chunk");
    }
    setParams(updated, { replace: true });
  };

  return (
    <div className="space-y-4">
      <PageHeader
        title="Knowledge"
        subtitle="Three tiers: the rules the analyser must follow, the prose it quotes from, and what this archive has learned about your kitchen."
      />
      <Tabs value={tab} onValueChange={setTab}>
        <TabsList>
          <TabsTrigger value="rules">Rules</TabsTrigger>
          <TabsTrigger value="docs">Docs</TabsTrigger>
          <TabsTrigger value="insights">Insights</TabsTrigger>
        </TabsList>
        <TabsContent value="rules">
          <RulesTab
            highlighted={highlighted}
            onClearHighlight={() => {
              const updated = new URLSearchParams(params);
              updated.delete("rule");
              setParams(updated, { replace: true });
            }}
          />
        </TabsContent>
        <TabsContent value="docs">
          <DocsTab slug={doc} onOpen={openDoc} highlightedChunk={chunk} />
        </TabsContent>
        <TabsContent value="insights">
          <InsightsTab />
        </TabsContent>
      </Tabs>
    </div>
  );
}

/**
 * The rule tier, by category, with a switch and an editor on each rule.
 *
 * Editing replaces the whole value document rather than merging: a merge cannot
 * express "remove this key", and the editor has the whole object in a text box
 * anyway. An edited rule keeps its text through every later re-seed — that is
 * what makes editing safe to do, and the badge says which rules are yours.
 */
function RulesTab({
  highlighted,
  onClearHighlight,
}: {
  highlighted: string | null;
  onClearHighlight: () => void;
}) {
  const [category, setCategory] = useState<string>("");
  const rules = useKnowledgeRules(category ? { category } : {});
  const reload = useReloadKnowledgeRules();
  useQueryErrorToast(rules.error, "Could not load the knowledge rules");

  const grouped = useMemo(() => {
    const out = new Map<string, KnowledgeRule[]>();
    for (const rule of rules.data?.items ?? []) {
      const bucket = out.get(rule.category);
      if (bucket) bucket.push(rule);
      else out.set(rule.category, [rule]);
    }
    return out;
  }, [rules.data]);

  const total = rules.data?.items.length ?? 0;
  const off = (rules.data?.items ?? []).filter((rule) => !rule.enabled).length;

  return (
    <div className="space-y-4">
      <SectionCard
        title="What the analyser is told"
        description="Every rule that matches a shot's bean, grinder, style and diagnostics is put in front of the model verbatim, and it is asked to name the ones it used. Turning a rule off removes it from the very next analysis; editing one keeps your text through every upgrade."
        actions={
          <Button
            size="sm"
            variant="outline"
            disabled={reload.isPending}
            onClick={() => reload.mutate()}
            data-testid="reload-rules"
          >
            <RefreshCw className="size-3.5" aria-hidden="true" />
            Reload from the file
          </Button>
        }
      >
        <p className="mb-2 text-muted-foreground text-sm" data-testid="rule-count">
          {rules.isPending
            ? "Loading the rule tier…"
            : `${total} rule${total === 1 ? "" : "s"}${off ? `, ${off} turned off` : ""}`}
        </p>
        <div className="flex flex-wrap gap-1.5" data-testid="category-filter">
          <FilterChip label="Everything" active={category === ""} onClick={() => setCategory("")} />
          {(rules.data?.categories ?? []).map((name) => (
            <FilterChip
              key={name}
              label={name.replace(/_/g, " ")}
              active={category === name}
              onClick={() => setCategory(name)}
            />
          ))}
        </div>
      </SectionCard>

      {rules.isPending ? (
        <Skeleton className="h-64 w-full" />
      ) : (
        [...grouped.entries()].map(([name, items]) => (
          <SectionCard key={name} title={name.replace(/_/g, " ")}>
            <ul className="space-y-2" data-testid="rule-list">
              {items.map((rule) => (
                <RuleRow
                  key={rule.id}
                  rule={rule}
                  highlighted={rule.key === highlighted}
                  onClearHighlight={onClearHighlight}
                />
              ))}
            </ul>
          </SectionCard>
        ))
      )}
    </div>
  );
}

function FilterChip({
  label,
  active,
  onClick,
}: {
  label: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className={cn(
        "rounded-full border px-2 py-0.5 text-xs transition-colors",
        active ? "border-foreground/30 bg-muted" : "border-border text-muted-foreground",
      )}
    >
      {label}
    </button>
  );
}

function RuleRow({
  rule,
  highlighted,
  onClearHighlight,
}: {
  rule: KnowledgeRule;
  highlighted: boolean;
  onClearHighlight: () => void;
}) {
  const patch = usePatchKnowledgeRule();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(() => JSON.stringify(rule.value ?? {}, null, 2));
  const [problem, setProblem] = useState("");

  return (
    <li
      data-testid="rule"
      data-rule={rule.key}
      data-enabled={rule.enabled}
      className={cn(
        "rounded-lg border p-3",
        highlighted ? "border-primary" : "border-border",
        rule.enabled ? undefined : "opacity-60",
      )}
    >
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div className="flex flex-wrap items-baseline gap-2">
          <span className="font-mono text-xs">{rule.key}</span>
          <Badge variant="outline">{rule.confidence}</Badge>
          {rule.edited ? <Badge variant="secondary">edited</Badge> : null}
          {rule.unit && rule.unit !== "none" ? (
            <span className="text-muted-foreground text-xs">{rule.unit}</span>
          ) : null}
        </div>
        <div className="flex items-center gap-2">
          <Button
            size="sm"
            variant="ghost"
            disabled={patch.isPending}
            data-testid="toggle-rule"
            onClick={() => patch.mutate({ id: rule.id, patch: { enabled: !rule.enabled } })}
          >
            {rule.enabled ? "Turn off" : "Turn on"}
          </Button>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => {
              setEditing((open) => !open);
              setProblem("");
            }}
          >
            {editing ? "Cancel" : "Edit"}
          </Button>
          {highlighted ? (
            <Button size="sm" variant="ghost" onClick={onClearHighlight}>
              Clear
            </Button>
          ) : null}
        </div>
      </div>

      <p className="mt-1.5 text-sm">{ruleText(rule)}</p>
      {rule.source ? (
        <p className="mt-1 text-muted-foreground text-xs">
          {rule.source}
          {rule.source_ref ? ` · ${rule.source_ref}` : ""}
        </p>
      ) : null}

      {editing ? (
        <form
          className="mt-2 space-y-2"
          onSubmit={async (event) => {
            event.preventDefault();
            let value: Record<string, unknown>;
            try {
              value = JSON.parse(draft);
            } catch (error) {
              // Parsed here rather than sent, so a stray comma is a message
              // beside the box instead of a 400 from three layers away.
              setProblem(error instanceof Error ? error.message : "that is not valid JSON");
              return;
            }
            setProblem("");
            const saved = await attempt(() => patch.mutateAsync({ id: rule.id, patch: { value } }));
            if (saved) setEditing(false);
          }}
        >
          <textarea
            className="h-40 w-full rounded-md border border-input bg-background p-2 font-mono text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            aria-label={`Value for ${rule.key}`}
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
          />
          {problem ? <p className="text-status-bad-text text-xs">{problem}</p> : null}
          <Button type="submit" size="sm" disabled={patch.isPending}>
            Save
          </Button>
        </form>
      ) : null}
    </li>
  );
}

/**
 * The rule's sentence.
 *
 * `text` inside the value is the reserved human form; a rule someone wrote
 * through the API without one falls back to its machine-readable keys, which is
 * exactly what the server does when it renders the rule into a prompt.
 */
function ruleText(rule: KnowledgeRule): string {
  const value = (rule.value ?? {}) as Record<string, unknown>;
  const text = value.text;
  if (typeof text === "string" && text.trim()) return text;
  return Object.entries(value)
    .filter(([key]) => key !== "text")
    .map(([key, entry]) => `${key}=${String(entry)}`)
    .join(", ");
}
