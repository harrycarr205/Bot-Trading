import type { ComponentType } from "react";
import Overview from "./pages/Overview";
import Positions from "./pages/Positions";
import TickerDetail from "./pages/TickerDetail";
import Decisions from "./pages/Decisions";
import DecisionDetail from "./pages/DecisionDetail";
import Orders from "./pages/Orders";
import Pnl from "./pages/Pnl";
import Control from "./pages/Control";
import Config from "./pages/Config";

export interface RouteDef {
  path: string;
  navLabel: string;
  navPath: string;
  Component: ComponentType;
}

export const routes: RouteDef[] = [
  { path: "/", navPath: "/", navLabel: "Overview", Component: Overview },
  { path: "/positions", navPath: "/positions", navLabel: "Positions", Component: Positions },
  { path: "/ticker/:symbol", navPath: "/positions", navLabel: "Positions", Component: TickerDetail },
  { path: "/decisions", navPath: "/decisions", navLabel: "Decisions", Component: Decisions },
  { path: "/decisions/:id", navPath: "/decisions", navLabel: "Decisions", Component: DecisionDetail },
  { path: "/orders", navPath: "/orders", navLabel: "Orders", Component: Orders },
  { path: "/pnl", navPath: "/pnl", navLabel: "P&L", Component: Pnl },
  { path: "/control", navPath: "/control", navLabel: "Control", Component: Control },
  { path: "/config", navPath: "/config", navLabel: "Config", Component: Config },
];

// Nav rail shows one entry per distinct navPath, in first-seen order.
export const navEntries: { navPath: string; navLabel: string }[] = routes
  .filter((r, i) => routes.findIndex((r2) => r2.navPath === r.navPath) === i)
  .map((r) => ({ navPath: r.navPath, navLabel: r.navLabel }));
