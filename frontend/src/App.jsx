import React, { lazy, Suspense } from "react";
import {
  BrowserRouter as Router,
  Routes,
  Route,
  Navigate,
} from "react-router-dom";
import MainLayout from "./layout/MainLayout";
import { getStoredUser } from "./lib/api";
import { Toaster } from "react-hot-toast";
import AppErrorBoundary from "./components/AppErrorBoundary";

const Login = lazy(() => import("./pages/Login"));
const ChangePassword = lazy(() => import("./pages/ChangePassword"));
const Dashboard = lazy(() => import("./pages/Dashboard"));
const Analytics = lazy(() => import("./pages/Analytics"));
const Settings = lazy(() => import("./pages/Settings"));
const DealingRisk = lazy(() => import("./pages/DealingRisk"));
const RiskCenter = lazy(() => import("./pages/RiskCenter"));
const DealerCommandCenter = lazy(() => import("./pages/DealerCommandCenter"));
const ManagerOverview = lazy(() => import("./pages/ManagerOverview"));
const MarketWatchPage = lazy(() => import("./pages/MarketWatchPage"));
const OperationsCenter = lazy(() => import("./pages/OperationsCenter"));
const RiskAutomation = lazy(() => import("./pages/RiskAutomation"));
const EconomicCalendar = lazy(() => import("./pages/EconomicCalendar"));
const BookShifting = lazy(() => import("./pages/BookShifting"));
const BBookPnL = lazy(() => import("./pages/BBookPnL"));
const BrokerageWorkspace = lazy(() => import("./pages/BrokerageWorkspace"));

function RouteFallback() {
  return <div className="min-h-screen" aria-label="Loading page" />;
}

function ProtectedRoute({ children, allowPasswordChange = false }) {
  const user = getStoredUser();
  if (!user) return <Navigate to="/login" replace />;
  if (user.must_change_pw && !allowPasswordChange)
    return <Navigate to="/change-password" replace />;
  return children;
}

function App() {
  return (
    <AppErrorBoundary>
      <Router>
        <Toaster
          position="top-right"
          containerStyle={{ top: 58, right: 16, zIndex: 120 }}
          toastOptions={{
            className: "rms-toast",
            duration: 5000,
            style: {
              background: "rgb(var(--color-card))",
              color: "rgb(var(--color-text))",
              border: "1px solid rgb(var(--color-border))",
              boxShadow: "var(--card-shadow)",
              borderRadius: "14px",
              maxWidth: "min(420px, calc(100vw - 32px))",
              overflowWrap: "anywhere",
              whiteSpace: "pre-line",
            },
            success: { iconTheme: { primary: "#49c9aa", secondary: "#00120d" } },
            error: { iconTheme: { primary: "#fb7185", secondary: "#1b0509" } },
          }}
        />
        <Suspense fallback={<RouteFallback />}>
          <Routes>
            <Route path="/login" element={<Login />} />
            <Route
              path="/change-password"
              element={
                <ProtectedRoute allowPasswordChange>
                  <ChangePassword />
                </ProtectedRoute>
              }
            />

            <Route
              path="/"
              element={
                <ProtectedRoute>
                  <MainLayout />
                </ProtectedRoute>
              }
            >
              <Route index element={<DealerCommandCenter />} />
              <Route path="command-center" element={<DealerCommandCenter />} />
              <Route
                path="brokerage"
                element={<Navigate to="/real-brokerage" replace />}
              />
              <Route path="deal-report" element={<Dashboard />} />
              <Route
                path="real-brokerage"
                element={<BrokerageWorkspace />}
              />
              <Route path="dealing-risk" element={<DealingRisk />} />
              <Route path="risk-center" element={<RiskCenter />} />
              <Route path="dealing-risk/analytics" element={<Navigate to="/risk-center" replace />} />
              <Route path="manager" element={<ManagerOverview />} />
              <Route path="market-watch" element={<MarketWatchPage />} />
              <Route path="operations" element={<OperationsCenter />} />
              <Route path="risk-automation" element={<RiskAutomation />} />
              <Route path="calendar" element={<EconomicCalendar />} />
              <Route path="bbook-pnl" element={<BBookPnL />} />
              <Route path="book-shifting" element={<BookShifting />} />
              <Route
                path="diff"
                element={<Navigate to="/real-brokerage" replace />}
              />
              <Route
                path="dashboard"
                element={<Navigate to="/deal-report" replace />}
              />
              <Route
                path="analytics"
                element={<Navigate to="/real-brokerage" replace />}
              />
              <Route
                path="equity-report"
                element={<Navigate to="/real-brokerage" replace />}
              />
              <Route path="settings" element={<Settings />} />
            </Route>

            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </Suspense>
      </Router>
    </AppErrorBoundary>
  );
}

export default App;
