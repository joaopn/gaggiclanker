import { useEffect } from "react";
import { Navigate, Route, Routes, useNavigate } from "react-router-dom";
import { AppShell } from "@/components/layout/AppShell";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { useEventInvalidation } from "@/hooks/useEventInvalidation";
import { buildSignInPath, setAuthNavigator } from "@/lib/auth-navigation";
import { DEFAULT_ROUTE } from "@/lib/navigation";
import { ImportPage } from "@/pages/ImportPage";
import { NotFoundPage } from "@/pages/NotFoundPage";
import { ProfilesPage } from "@/pages/ProfilesPage";
import { BeansPage, HardwarePage, KnowledgePage, SetsPage } from "@/pages/placeholders";
import { ShotsPage } from "@/pages/ShotsPage";
import { SettingsPage } from "@/pages/settings/SettingsPage";

/**
 * The device's live stream. It carries two kinds of event: `device.live`
 * at 2 Hz, which a consumer reads directly, and `device.connection`, which is
 * the one mapped to a query invalidation in `lib/invalidate.ts`.
 */
const DEVICE_STREAM_URL: string | null = "/api/device/live";

/**
 * The sync engine's stream: a shot ingested, a shot quarantined, a
 * profile changed, a run started or finished.
 *
 * A second EventSource rather than one merged stream, because the two have
 * completely different tempos — telemetry at 2 Hz against a handful of events
 * an hour — and a browser that dropped the busy one would take the quiet one
 * with it.
 */
const SYNC_STREAM_URL: string | null = "/api/sync/events";

export function App() {
  const navigate = useNavigate();

  // Let the API client send an expired session to the sign-in page through the
  // router rather than a full page load.
  //
  // There is no /sign-in route yet -- auth, the login endpoint and that page all
  // arrive with auth. Until then nothing answers UNAUTHORIZED, so this never fires;
  // registering it now means auth adds a route and a page, and no call site.
  useEffect(() => {
    setAuthNavigator((nextPath) => navigate(buildSignInPath(nextPath), { replace: true }));
    return () => setAuthNavigator(null);
  }, [navigate]);

  useEventInvalidation(DEVICE_STREAM_URL);
  useEventInvalidation(SYNC_STREAM_URL);

  return (
    <TooltipProvider delayDuration={200}>
      <Routes>
        <Route element={<AppShell />}>
          <Route path="/" element={<Navigate to={DEFAULT_ROUTE} replace />} />
          <Route path="/shots" element={<ShotsPage />} />
          <Route path="/sets" element={<SetsPage />} />
          <Route path="/beans" element={<BeansPage />} />
          <Route path="/hardware" element={<HardwarePage />} />
          <Route path="/profiles" element={<ProfilesPage />} />
          <Route path="/import" element={<ImportPage />} />
          <Route path="/knowledge" element={<KnowledgePage />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="*" element={<NotFoundPage />} />
        </Route>
      </Routes>
      <Toaster position="bottom-right" richColors closeButton />
    </TooltipProvider>
  );
}
