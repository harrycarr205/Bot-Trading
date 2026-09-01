import { usePolling } from "../hooks/usePolling";
import { api } from "../api/client";
import { StatTile } from "../components/StatTile";
import type { PanelDefinition } from "./types";

function EquityPanel() {
  const { data } = usePolling(api.overview, 30_000);
  const value = data?.snapshot ? `$${data.snapshot.equity.toLocaleString("en-US", { minimumFractionDigits: 2 })}` : "—";
  return <StatTile label="Equity" value={value} />;
}

export const panel: PanelDefinition = {
  id: "equity", title: "Equity", defaultSize: { w: 3, h: 2 }, Component: EquityPanel,
};
