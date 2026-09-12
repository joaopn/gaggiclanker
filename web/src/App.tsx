import { useEffect } from "react";
import { Navigate, Route, Routes, useNavigate } from "react-router-dom";
import { AppShell } from "@/components/layout/AppShell";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { useEventInvalidation } from "@/hooks/useEventInvalidation";
import { buildSignInPath, setAuthNavigator } from "@/lib/auth-navigation";
import { DEFAULT_ROUTE } from "@/lib/navigation";
import { NotFoundPage } from "@/pages/NotFoundPage";
import {
  BeansPage,
  HardwarePage,
  ImportPage,
  KnowledgePage,
  ProfilesPage,
  SetsPage,
  ShotsPage,
} from "@/pages/placeholders";
import { SettingsPage } from "@/pages/settings/SettingsPage";

/**
 * The event stream lands with the device client. Pointing at it now is harmless — `useSse`
 * with a null url does nothing — and means the wiring is already proven when
 * the endpoint appears.
 */
const EVENT_STREAM_URL: string | null = null;

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

  useEventInvalidation(EVENT_STREAM_URL);

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
