import { Loader2, ShieldCheck } from "lucide-react";
import { useState } from "react";
import { type Control, Controller, type FieldErrors, useWatch } from "react-hook-form";
import type { ResolvedSetting } from "@/api/types";
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
import { useLlmModels, useLlmStatus, useResetRateLimit, useValidateLlm } from "@/hooks/useLlm";
import { SettingField } from "@/pages/settings/SettingField";
import { humanizeKey, type SettingsFormValues } from "@/pages/settings/schema";

/**
 * Providers whose endpoint the user may point somewhere else.
 *
 * Mirrors `Preset.allows_base_url` in `providers/openai_compatible.py`, and for
 * the same reason: redirecting a hosted provider while still sending its key is
 * how an API key ends up on somebody else's server. The backend ignores the
 * value for the others, so this only hides a box that would do nothing.
 */
const BASE_URL_PROVIDERS = new Set(["ollama", "lmstudio", "openai_compatible"]);

/** Which key holds which provider's credential. Mirrors `credential_for`. */
const KEY_FOR_PROVIDER: Record<string, string> = {
  anthropic: "anthropicApiKey",
  claude_code: "claudeCodeOauthToken",
};

const PROVIDER_LABELS: Record<string, string> = {
  openrouter: "OpenRouter",
  openai: "OpenAI",
  ollama: "Ollama (local)",
  lmstudio: "LM Studio (local)",
  openai_compatible: "Any OpenAI-compatible gateway",
  anthropic: "Anthropic API",
  claude_code: "Claude Code CLI (subscription)",
};

const MODEL_KEY_ORDER = ["modelDefault", "modelAnalysis", "modelDraft", "modelChat"];

/** Keys the Claude Code panel owns, so the generic list does not repeat them. */
const CLAUDE_CODE_KEYS = new Set(["claudeCodeBin", "claudeCodeEffort"]);

function isRelevant(key: string, provider: string): boolean {
  if (key === "llmBaseUrl") return BASE_URL_PROVIDERS.has(provider);
  if (key === "llmApiKey") return !(provider in KEY_FOR_PROVIDER);
  if (key === "anthropicApiKey") return provider === "anthropic";
  if (key === "claudeCodeOauthToken") return provider === "claude_code";
  return true;
}

/**
 * The LLM section of the settings page.
 *
 * The registry fields are still rendered by the shared `SettingField`, so the
 * form, its validation and its "only send what changed" patch all keep working
 * unchanged. What this adds is the three things a generated form cannot know:
 * which fields are irrelevant to the chosen provider, that some of them are a
 * closed set and want a picker, and that a credential is worth testing before
 * an analysis fails at midnight.
 */
export function LlmSection({
  entries,
  control,
  errors,
  disabled,
}: {
  entries: ResolvedSetting[];
  control: Control<SettingsFormValues>;
  errors: FieldErrors<SettingsFormValues>;
  disabled?: boolean;
}) {
  const status = useLlmStatus();
  const validate = useValidateLlm();
  const resetRateLimit = useResetRateLimit();
  const [wantModels, setWantModels] = useState(false);

  // The provider as the form currently has it, not as the server last saw it:
  // switching the picker should hide the irrelevant boxes immediately, before
  // anything is saved.
  const provider = String(
    useWatch({ control, name: "llmProvider", defaultValue: "" }) || "claude_code",
  );
  const models = useLlmModels(provider, wantModels);

  const byKey = new Map(entries.map((entry) => [entry.key, entry]));
  const providerField = byKey.get("llmProvider");
  const modelFields = MODEL_KEY_ORDER.map((key) => byKey.get(key)).filter(
    (entry): entry is ResolvedSetting => entry !== undefined,
  );
  const rest = entries.filter(
    (entry) =>
      entry.key !== "llmProvider" &&
      !MODEL_KEY_ORDER.includes(entry.key) &&
      !CLAUDE_CODE_KEYS.has(entry.key) &&
      isRelevant(entry.key, provider),
  );

  const rateLimit = status.data?.rate_limit;
  const claudeCode = status.data?.claude_code as
    | { version?: string; authenticated?: boolean; detail?: string }
    | undefined;

  return (
    <div className="space-y-6">
      {providerField ? (
        <div className="space-y-1.5">
          <Label htmlFor="setting-llmProvider">Provider</Label>
          <Controller
            name="llmProvider"
            control={control}
            render={({ field }) => (
              <Select
                value={String(field.value || "claude_code")}
                onValueChange={field.onChange}
                disabled={disabled}
              >
                <SelectTrigger id="setting-llmProvider" aria-label="Provider">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {(status.data?.providers ?? Object.keys(PROVIDER_LABELS)).map((id) => (
                    <SelectItem key={id} value={id}>
                      {PROVIDER_LABELS[id] ?? id}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}
          />
          <p className="text-muted-foreground text-xs">{providerField.description}</p>
        </div>
      ) : null}

      {rest.map((setting) => (
        <SettingField
          key={setting.key}
          setting={setting}
          control={control}
          error={errors[setting.key]}
          disabled={disabled}
        />
      ))}

      <div className="flex flex-wrap items-center gap-3">
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => validate.mutate(undefined)}
          disabled={validate.isPending}
        >
          {validate.isPending ? (
            <Loader2 className="size-4 animate-spin" />
          ) : (
            <ShieldCheck className="size-4" />
          )}
          Validate credentials
        </Button>
        <p className="text-muted-foreground text-xs">
          Asks the provider the cheapest question it answers - a model list, or{" "}
          <code>claude auth status</code>. Save first: it tests what is stored, not what is typed.
        </p>
      </div>

      {/* Models per purpose. */}
      <div className="space-y-4 border-border border-t pt-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <h4 className="font-medium text-sm">Models</h4>
            <p className="text-muted-foreground text-xs">
              Each purpose falls back to the default, and the default falls back to whatever the
              provider picks.
            </p>
          </div>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => setWantModels(true)}
            disabled={models.isFetching}
          >
            {models.isFetching ? "Listing..." : "List models"}
          </Button>
        </div>

        {wantModels && models.data ? (
          <div
            className="flex flex-wrap gap-1.5 rounded-md border border-border p-2"
            data-testid="model-suggestions"
          >
            {models.data.models.length === 0 ? (
              <span className="text-muted-foreground text-xs">
                The provider offered no model list.
              </span>
            ) : (
              models.data.models.slice(0, 40).map((id) => (
                <Badge key={id} variant="secondary" className="font-mono font-normal text-[10px]">
                  {id}
                </Badge>
              ))
            )}
          </div>
        ) : null}

        {modelFields.map((setting) => (
          <SettingField
            key={setting.key}
            setting={setting}
            control={control}
            error={errors[setting.key]}
            disabled={disabled}
          />
        ))}
      </div>

      {/* The Claude Code panel, only when it is the provider in use. */}
      {provider === "claude_code" ? (
        <div className="space-y-4 border-border border-t pt-4" data-testid="claude-code-panel">
          <div className="flex flex-wrap items-center gap-2">
            <h4 className="font-medium text-sm">Claude Code CLI</h4>
            <Badge variant="outline" className="font-normal text-[10px]">
              {claudeCode?.version || "version unknown"}
            </Badge>
            <Badge
              variant={claudeCode?.authenticated ? "secondary" : "destructive"}
              className="font-normal text-[10px]"
            >
              {claudeCode?.authenticated ? "authenticated" : "not authenticated"}
            </Badge>
          </div>
          {claudeCode?.detail ? (
            <p className="text-muted-foreground text-xs">{claudeCode.detail}</p>
          ) : null}
          {["claudeCodeBin", "claudeCodeEffort"].map((key) => {
            const setting = byKey.get(key);
            if (!setting) return null;
            if (key !== "claudeCodeEffort") {
              return (
                <SettingField
                  key={key}
                  setting={setting}
                  control={control}
                  error={errors[key]}
                  disabled={disabled}
                />
              );
            }
            return (
              <div className="space-y-1.5" key={key}>
                <Label htmlFor="setting-claudeCodeEffort">{humanizeKey(key)}</Label>
                <Controller
                  name={key}
                  control={control}
                  render={({ field }) => (
                    <Select
                      value={String(field.value || "auto")}
                      onValueChange={(value) => field.onChange(value === "auto" ? "" : value)}
                      disabled={disabled}
                    >
                      <SelectTrigger id="setting-claudeCodeEffort" aria-label="Claude code effort">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="auto">Let the CLI decide</SelectItem>
                        {(status.data?.effort_levels ?? []).map((level) => (
                          <SelectItem key={level} value={level}>
                            {level}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  )}
                />
                <p className="text-muted-foreground text-xs">{setting.description}</p>
              </div>
            );
          })}
        </div>
      ) : null}

      {/* The rate-limit latch. */}
      <div className="space-y-2 border-border border-t pt-4">
        <div className="flex flex-wrap items-center gap-2">
          <h4 className="font-medium text-sm">Rate limit</h4>
          <Badge
            variant={rateLimit?.stopped ? "destructive" : "secondary"}
            className="font-normal text-[10px]"
            data-testid="rate-limit-state"
          >
            {rateLimit?.stopped
              ? "stopped"
              : `${rateLimit?.remaining ?? "-"} of ${rateLimit?.retries ?? "-"} retries left`}
          </Badge>
        </div>
        <p className="text-muted-foreground text-xs">
          When the provider throttles the account, the whole process stops rather than failing every
          queued shot in turn. Clearing it is deliberate: an automatic timer would just walk back
          into the same wall.
        </p>
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => resetRateLimit.mutate()}
          disabled={resetRateLimit.isPending}
        >
          {resetRateLimit.isPending ? "Clearing..." : "Clear the rate-limit stop"}
        </Button>
      </div>
    </div>
  );
}
