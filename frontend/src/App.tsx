import { lazy, Suspense, useCallback, useEffect, useState } from "react";
import { clearAuthSession, hasAuthSession, isAuthRequiredError, isDevelopmentMockMode } from "./api";
import { AppShell } from "./components/AppShell";
import { PageState } from "./components/PageState";
import type { DesignPage } from "./lib/constants";
import { getInitialPage } from "./lib/constants";

const LoginPage = lazy(() => import("./components/pages/LoginPage").then((module) => ({ default: module.LoginPage })));
const DashboardPage = lazy(() => import("./components/pages/DashboardPage").then((module) => ({ default: module.DashboardPage })));
const AddPlantPage = lazy(() => import("./components/pages/AddPlantPage").then((module) => ({ default: module.AddPlantPage })));
const PlantDetailPage = lazy(() => import("./components/pages/PlantDetailPage").then((module) => ({ default: module.PlantDetailPage })));
const ChatPage = lazy(() => import("./components/pages/ChatPage").then((module) => ({ default: module.ChatPage })));
const GardenPage = lazy(() => import("./components/pages/GardenPage").then((module) => ({ default: module.GardenPage })));

function normalizePage(page: DesignPage): DesignPage {
  if (page === "login") return isDevelopmentMockMode() ? "login" : hasAuthSession() ? "dashboard" : "login";
  return hasAuthSession() ? page : "login";
}

export function App() {
  const [page, setPage] = useState<DesignPage>(() => normalizePage(getInitialPage()));

  useEffect(() => {
    const handleHashChange = () => setPage(normalizePage(getInitialPage()));
    window.addEventListener("hashchange", handleHashChange);
    return () => window.removeEventListener("hashchange", handleHashChange);
  }, []);

  useEffect(() => {
    window.scrollTo({ top: 0, behavior: "auto" });
  }, [page]);

  const navigate = useCallback((nextPage: DesignPage) => {
    const guardedPage = normalizePage(nextPage);
    window.location.hash = guardedPage;
    setPage(guardedPage);
  }, []);

  const navigateToDashboardSection = useCallback((sectionId: string) => {
    navigate("dashboard");
    let attempts = 0;
    const scrollWhenReady = () => {
      const target = document.getElementById(sectionId);
      if (target) {
        target.setAttribute("tabindex", "-1");
        target.scrollIntoView({ behavior: "smooth", block: "start" });
        target.focus({ preventScroll: true });
        return;
      }
      attempts += 1;
      if (attempts < 50) window.setTimeout(scrollWhenReady, 80);
    };
    window.setTimeout(scrollWhenReady, 0);
  }, [navigate]);

  const handleAuthenticated = useCallback(() => {
    navigate("dashboard");
  }, [navigate]);

  const handleLogout = useCallback(() => {
    clearAuthSession();
    navigate("login");
  }, [navigate]);

  const handlePageError = useCallback((error: unknown) => {
    if (isAuthRequiredError(error)) {
      clearAuthSession();
      navigate("login");
      return true;
    }
    return false;
  }, [navigate]);

  const loadingFallback = <PageState kind="loading" title="화면을 준비하고 있어요" />;

  if (page === "login") {
    return (
      <Suspense fallback={loadingFallback}>
        <LoginPage onAuthenticated={handleAuthenticated} />
      </Suspense>
    );
  }

  return (
    <AppShell
      currentPage={page}
      onNavigate={navigate}
      onNavigateToDashboardSection={navigateToDashboardSection}
      onLogout={handleLogout}
    >
      <div className="page-transition" key={page}>
        <Suspense fallback={loadingFallback}>
          {page === "dashboard" && <DashboardPage onNavigate={navigate} onAuthError={handlePageError} />}
          {page === "add" && <AddPlantPage onNavigate={navigate} onAuthError={handlePageError} />}
          {page === "detail" && <PlantDetailPage onNavigate={navigate} onAuthError={handlePageError} />}
          {page === "chat" && <ChatPage onNavigate={navigate} onAuthError={handlePageError} />}
          {page === "garden" && <GardenPage onNavigate={navigate} onAuthError={handlePageError} />}
        </Suspense>
      </div>
    </AppShell>
  );
}
