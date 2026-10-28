import React, { Suspense, useEffect, useState } from "react";
import { Outlet, useLocation, useOutlet } from "react-router-dom";
import Sidebar from "../components/Sidebar";
import AppErrorBoundary from "../components/AppErrorBoundary";
import InAppNotifications from "../components/InAppNotifications";

const PERSISTED_PAGES = new Set([
  "/risk-center",
  "/command-center", "/dealing-risk", "/manager", "/market-watch",
  "/operations", "/book-shifting", "/risk-automation", "/calendar", "/bbook-pnl",
  "/deal-report", "/real-brokerage", "/settings",
]);

function PersistentOutlet() {
  const location = useLocation();
  const outlet = useOutlet();
  const path = location.pathname === "/" ? "/command-center" : location.pathname;
  const [initialPath] = useState(path);
  const [visited, setVisited] = useState([]);

  useEffect(() => {
    if (!PERSISTED_PAGES.has(path)) return;
    // Record a page once after its first render; the matching keyed wrapper
    // already exists in this render, so this update does not remount it.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setVisited((current) => current.some((page) => page.path === path)
      ? current : [...current, { path, outlet }]);
  }, [path, outlet]);

  if (!PERSISTED_PAGES.has(path)) return <Outlet />;
  const pages = visited.some((page) => page.path === path)
    ? visited : [...visited, { path, outlet }];
  return pages.map((page) => (
    <div key={page.path} hidden={page.path !== path}>
      <AppErrorBoundary>
        <Suspense fallback={page.path === initialPath
          ? <div className="py-6 text-sm text-muted" role="status">Loading section...</div>
          : null}>
          {page.path === path ? outlet : page.outlet}
        </Suspense>
      </AppErrorBoundary>
    </div>
  ));
}

export default function MainLayout() {
  return (
    <div
      className="relative flex h-dvh overflow-hidden"
      style={{
        background: "rgb(var(--color-bg))",
        color: "rgb(var(--color-text))",
      }}
    >
      <Sidebar />
      <InAppNotifications />
      <div className="flex flex-1 flex-col relative z-10 min-w-0">
        <main tabIndex={0} aria-label="Main content" className="scrollbar-hidden min-w-0 flex-1 overflow-y-auto px-3 pb-6 pt-16 sm:px-5 md:px-6 md:py-6 xl:px-8">
          <div className="mx-auto w-full max-w-[1720px] min-w-0">
            <PersistentOutlet />
          </div>
        </main>
      </div>
    </div>
  );
}
