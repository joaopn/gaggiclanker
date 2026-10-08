import { lazy, Suspense } from "react";
import { Skeleton } from "@/components/ui/skeleton";
import { hasCurve } from "@/lib/profileCurve";

/** Chart.js loads once, when the first curve is wanted: a closed list never downloads it. */
const ProfileCurveChart = lazy(() =>
  import("@/components/charts/ProfileCurveChart").then((module) => ({
    default: module.ProfileCurveChart,
  })),
);

/**
 * The curve of a profile document, where the machine draws one: "pro" profiles only. Any other
 * profile gets nothing here (not even the chart's code), and its summary stays as it was.
 */
export function ProfileCurve({
  profile,
  title,
  xMax,
}: {
  profile: Record<string, unknown> | null | undefined;
  title?: string;
  xMax?: number;
}) {
  if (!profile || !hasCurve(profile)) return null;
  return (
    <Suspense
      fallback={<Skeleton className="h-[200px] w-full" data-testid="profile-curve-loading" />}
    >
      <ProfileCurveChart profile={profile} title={title} xMax={xMax} />
    </Suspense>
  );
}
