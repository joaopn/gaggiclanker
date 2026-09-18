import { lazy, Suspense, useEffect } from "react";
import { Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { AppShell } from "@/components/layout/AppShell";
import { Skeleton } from "@/components/ui/skeleton";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { useEventInvalidation } from "@/hooks/useEventInvalidation";
import { buildSignInPath, setAuthNavigator } from "@/lib/auth-navigation";
import { DEFAULT_ROUTE } from "@/lib/navigation";
import { DEFAULT_SETTINGS_PAGE, settingsPath } from "@/lib/settingsPages";
import { BeansPage } from "@/pages/BeansPage";
import { ChatPage } from "@/pages/ChatPage";
import { DevicePage } from "@/pages/DevicePage";
import { HardwarePage } from "@/pages/HardwarePage";
import { KnowledgePage } from "@/pages/KnowledgePage";
import { NotFoundPage } from "@/pages/NotFoundPage";
import { ProfilesPage } from "@/pages/ProfilesPage";
import { SetsPage } from "@/pages/SetsPage";
import { ShotsPage } from "@/pages/ShotsPage";
import { SignInPage } from "@/pages/SignInPage";
import { SyncPage } from "@/pages/SyncPage";
import { SettingsPage } from "@/pages/settings/SettingsPage";

/**
 * The shot page is the only route that needs Chart.js, and Chart.js is the
 * largest thing in the bundle. Splitting it here means opening the list does
 * not download a charting library, and the fetch happens while the shot's own
 * request is in flight.
 */
const ShotDetailPage = lazy(() =>
  import("@/pages/ShotDetailPage").then((module) => ({ default: module.ShotDetailPage })),
);

/** The other route that draws a chart, split for the same reason. */
const SetDetailPage = lazy(() =>
  import("@/pages/SetDetailPage").then((module) => ({ default: module.SetDetailPage })),
);

/**
 * The sync engine's stream: a shot ingested, a shot quarantined, a profile
 * changed, a pull started or finished. The only stream the app subscribes to —
 * the device's 2 Hz telemetry used to be a second one, and drawing the shot
 * that is happening now is the machine's own web UI's job.
 */
const SYNC_STREAM_URL: string | null = "/api/sync/events";

export function App() {
  const navigate = useNavigate();
  const { pathname } = useLocation();

  // No stream on the sign-in page. It would 401, which the SSE helper treats
  // as fatal and handles by sending the user to sign in — where they already
  // are. Nothing breaks, but a pointless request and a redirect-to-self on
  // every failed login is not what the page should be doing.
  const signedOut = pathname === "/sign-in";

  // Let the API client send an expired session to the sign-in page through the
  // router rather than a full page load, which would throw away the query cache
  // and flash white. Registered here because `fetchApi` is a plain module with
  // no hooks and no router context.
  useEffect(() => {
    setAuthNavigator((nextPath) => navigate(buildSignInPath(nextPath), { replace: true }));
    return () => setAuthNavigator(null);
  }, [navigate]);

  useEventInvalidation(signedOut ? null : SYNC_STREAM_URL);

  return (
    <TooltipProvider delayDuration={200}>
      <Routes>
        {/* Outside AppShell: the shell's own queries need a token, so rendering
            it around the sign-in page would 401 the user straight back here. */}
        <Route path="/sign-in" element={<SignInPage />} />
        <Route element={<AppShell />}>
          <Route path="/" element={<Navigate to={DEFAULT_ROUTE} replace />} />
          <Route path="/shots" element={<ShotsPage />} />
          <Route
            path="/shots/:shotId"
            element={
              <Suspense fallback={<Skeleton className="h-72 w-full" />}>
                <ShotDetailPage />
              </Suspense>
            }
          />
          <Route path="/device" element={<DevicePage />} />
          <Route path="/sync" element={<SyncPage />} />
          <Route path="/sets" element={<SetsPage />} />
          <Route
            path="/sets/:setId"
            element={
              <Suspense fallback={<Skeleton className="h-72 w-full" />}>
                <SetDetailPage />
              </Suspense>
            }
          />
          <Route path="/beans" element={<BeansPage />} />
          <Route path="/hardware" element={<HardwarePage />} />
          <Route path="/profiles" element={<ProfilesPage />} />
          {/* The draft queue is a section of the profiles page now. The route
              stays as a redirect: a bookmark, and a hard refresh on one, land
              on the queue rather than on a 404. */}
          <Route path="/drafts" element={<Navigate to="/profiles#staged" replace />} />
          <Route path="/chat" element={<ChatPage />} />
          {/* The import page is gone — the drop zone on the shots page is the
              whole feature now. The route stays as a redirect so an old
              bookmark, and a hard refresh on one, land somewhere useful. */}
          <Route path="/import" element={<Navigate to="/shots" replace />} />
          <Route path="/knowledge" element={<KnowledgePage />} />
          {/* Settings is a group of pages; the bare path, and the `g ,` chord that
              goes to it, land on the first one. */}
          <Route
            path="/settings"
            element={<Navigate to={settingsPath(DEFAULT_SETTINGS_PAGE)} replace />}
          />
          <Route path="/settings/:page" element={<SettingsPage />} />
          <Route path="*" element={<NotFoundPage />} />
        </Route>
      </Routes>
      <Toaster position="bottom-right" richColors closeButton />
    </TooltipProvider>
  );
}
