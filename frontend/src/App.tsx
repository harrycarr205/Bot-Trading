import { Route, Routes, useLocation } from "react-router-dom";
import { NavRail } from "./components/NavRail";
import { TickerStrip } from "./components/TickerStrip";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { routes } from "./routes";

export default function App() {
  const location = useLocation();

  return (
    <div>
      <TickerStrip />
      <div style={{ display: "flex" }}>
        <NavRail />
        <main style={{ flex: 1, padding: 24 }}>
          {/* Keyed on the path so navigating remounts the boundary and clears
              its error state. NavRail sits outside the boundary, so without
              this a single route throwing leaves "Data unavailable" rendered
              for every subsequent nav click until a full page reload. */}
          <ErrorBoundary key={location.pathname}>
            <Routes>
              {routes.map((r) => (
                <Route key={r.path} path={r.path} element={<r.Component />} />
              ))}
            </Routes>
          </ErrorBoundary>
        </main>
      </div>
    </div>
  );
}
