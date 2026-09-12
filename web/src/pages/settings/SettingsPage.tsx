import { zodResolver } from "@hookform/resolvers/zod";
import { AlertTriangle } from "lucide-react";
import { useEffect, useMemo, useRef } from "react";
import { useForm } from "react-hook-form";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import type { ResolvedSetting } from "@/api/types";
import { EmptyState } from "@/components/layout/EmptyState";
import { PageHeader } from "@/components/layout/PageHeader";
import { SectionCard } from "@/components/layout/SectionCard";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useHealth } from "@/hooks/useHealth";
import { useQueryErrorToast } from "@/hooks/useQueryErrorToast";
import { useCreateBackup, useSettings, useUpdateSettings } from "@/hooks/useSettings";
import { AuthSection } from "@/pages/settings/AuthSection";
import { LlmSection } from "@/pages/settings/LlmSection";
import { PromptsSection } from "@/pages/settings/PromptsSection";
import { SettingField } from "@/pages/settings/SettingField";
import {
  buildSettingsSchema,
  SETTINGS_SECTIONS,
  type SettingsFormValues,
  sectionFor,
  toFormValues,
  toPatch,
} from "@/pages/settings/schema";

/**
 * The first real page: it proves the API client, the envelope unwrap, the form
 * stack and the toasts against a backend that already exists.
 *
 * The form is generated from the registry `GET /api/settings` returns. Adding a
 * setting in `gaggiclanker/settings.py` makes it appear here with the right
 * control and the right validation — that is the whole point of the registry.
 */
export function SettingsPage() {
  const settingsQuery = useSettings();
  const update = useUpdateSettings();
  const backup = useCreateBackup();
  const health = useHealth();

  useQueryErrorToast(settingsQuery.error, "Could not load settings");

  const settings = settingsQuery.data;

  const schema = useMemo(() => (settings ? buildSettingsSchema(settings) : undefined), [settings]);

  const form = useForm<SettingsFormValues>({
    // The resolver depends on data that arrives after the first render, so it
    // is swapped in rather than built once — react-hook-form reads it per
    // submit, which is why this works.
    resolver: schema ? zodResolver(schema) : undefined,
    defaultValues: {},
    mode: "onSubmit",
  });

  const { reset } = form;

  // Hydrate once, and then only while the form is pristine.
  //
  // Every refetch hands back a fresh object, and `onSettled` refetches after a
  // FAILED save too — so a plain `reset` on every change silently threw away
  // what the user had just typed, at precisely the moment they most wanted to
  // keep it and retry. The dirty flag is read through a ref so it does not
  // become an effect dependency and re-trigger the reset it just caused.
  const isDirtyRef = useRef(false);
  isDirtyRef.current = form.formState.isDirty;
  useEffect(() => {
    if (settings && !isDirtyRef.current) reset(toFormValues(settings));
  }, [settings, reset]);

  const grouped = useMemo(() => {
    const map = new Map<string, ResolvedSetting[]>();
    for (const setting of Object.values(settings ?? {})) {
      const section = sectionFor(setting.key);
      const bucket = map.get(section) ?? [];
      bucket.push(setting);
      map.set(section, bucket);
    }
    return map;
  }, [settings]);

  const onSubmit = form.handleSubmit(async (values) => {
    if (!settings) return;
    const patch = toPatch(settings, values);
    if (Object.keys(patch).length === 0) {
      toast.info("Nothing to save");
      return;
    }
    try {
      const next = await update.mutateAsync(patch);
      // Re-seed from the server's answer: it is what decided the new source of
      // each key, and it blanks the secret boxes again.
      reset(toFormValues(next));
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not save settings");
    }
  });

  return (
    <div className="space-y-6">
      <PageHeader
        title="Settings"
        subtitle="The runtime registry: database over environment over default."
        actions={
          <Button onClick={onSubmit} disabled={!settings || update.isPending}>
            {update.isPending ? "Saving..." : "Save changes"}
          </Button>
        }
      />

      {settingsQuery.isPending ? (
        <div className="space-y-3" data-testid="settings-skeleton">
          <Skeleton className="h-32 w-full" />
          <Skeleton className="h-32 w-full" />
        </div>
      ) : settingsQuery.isError ? (
        <EmptyState
          icon={AlertTriangle}
          title="Could not load settings"
          description={settingsQuery.error.message}
          action={
            <Button variant="outline" onClick={() => void settingsQuery.refetch()}>
              Try again
            </Button>
          }
        />
      ) : (
        <form onSubmit={onSubmit} className="space-y-6" noValidate>
          {SETTINGS_SECTIONS.map((section) => {
            const entries = grouped.get(section.id) ?? [];
            if (entries.length === 0) return null;
            return (
              <SectionCard
                key={section.id}
                title={section.title}
                description={section.description}
                contentClassName="space-y-5"
              >
                {section.id === "auth" ? (
                  // The password is not a registry field and must not be one:
                  // see AuthSection. The other auth keys still go through the
                  // generated form.
                  <AuthSection
                    entries={entries}
                    control={form.control}
                    errors={form.formState.errors}
                    disabled={update.isPending}
                  />
                ) : section.id === "llm" ? (
                  // The LLM keys get their own component: which of them matter
                  // depends on the provider, two are a closed set, and a
                  // credential is worth testing before an analysis fails at
                  // midnight. Everything else is still the generated form.
                  <LlmSection
                    entries={entries}
                    control={form.control}
                    errors={form.formState.errors}
                    disabled={update.isPending}
                  />
                ) : (
                  entries.map((setting) => (
                    <SettingField
                      key={setting.key}
                      setting={setting}
                      control={form.control}
                      error={form.formState.errors[setting.key]}
                      disabled={update.isPending}
                    />
                  ))
                )}
              </SectionCard>
            );
          })}
        </form>
      )}

      <PromptsSection />

      <SectionCard
        title="Import"
        description="Shots and profiles exported from the machine's own web UI, including ones it has since deleted."
        contentClassName="space-y-3"
      >
        <p className="text-muted-foreground text-sm">
          The machine keeps about 300 KB of history and drops the oldest shots when it runs short.
          An export saved before that happened is the only way those shots come back.
        </p>
        <Button asChild variant="outline">
          <Link to="/import">Open the import page</Link>
        </Button>
      </SectionCard>

      <SectionCard
        title="System"
        description="What the backend reports about itself, and a copy of the database on demand."
        contentClassName="space-y-4"
      >
        <dl className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-3">
          <div>
            <dt className="text-muted-foreground text-xs">Status</dt>
            <dd data-testid="health-status">
              {health.data?.status ?? (health.isError ? "unreachable" : "...")}
            </dd>
          </div>
          <div>
            <dt className="text-muted-foreground text-xs">Version</dt>
            <dd data-testid="health-version">{health.data?.version ?? "-"}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground text-xs">Database</dt>
            <dd data-testid="health-database">{health.data?.database ?? "-"}</dd>
          </div>
        </dl>
        <div className="flex flex-wrap items-center gap-3">
          <Button
            variant="outline"
            onClick={() => backup.mutate()}
            disabled={backup.isPending}
            type="button"
          >
            {backup.isPending ? "Backing up..." : "Back up database"}
          </Button>
          <p className="text-muted-foreground text-xs">
            Written with <code>VACUUM INTO</code> under the data directory, so it lands on the host
            bind mount immediately.
          </p>
        </div>
      </SectionCard>
    </div>
  );
}
