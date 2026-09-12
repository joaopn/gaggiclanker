import type { LucideIcon } from "lucide-react";
import { EmptyState } from "@/components/layout/EmptyState";
import { PageHeader } from "@/components/layout/PageHeader";

/**
 * A route that exists so navigation, shortcuts and deep links can be tested
 * end to end before the page behind it is written. Each one names the chunk
 * that replaces it, so an empty screen reads as "not yet" rather than "broken".
 */
export function PlaceholderPage({
  title,
  subtitle,
  icon,
  chunk,
  detail,
}: {
  title: string;
  subtitle: string;
  icon: LucideIcon;
  chunk: string;
  detail: string;
}) {
  return (
    <div className="space-y-6">
      <PageHeader title={title} subtitle={subtitle} />
      <EmptyState icon={icon} title={`${title} arrives in ${chunk}`} description={detail} />
    </div>
  );
}
