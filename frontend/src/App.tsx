import { Route, Routes } from "react-router-dom";
import { NavRail } from "./components/NavRail";
import { TickerStrip } from "./components/TickerStrip";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { routes } from "./routes";

export default function App() {
  return (
    <div>
      <TickerStrip />
      <div style={{ display: "flex" }}>
        <NavRail />
        <main style={{ flex: 1, padding: 24 }}>
          <ErrorBoundary>
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
