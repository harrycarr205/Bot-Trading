import { usePolling } from "../hooks/usePolling";
import { api } from "../api/client";
import { StatTile } from "../components/StatTile";
import { DataUnavailable } from "../components/DataUnavailable";
import type { PanelDefinition } from "./types";

const TITLE = "Equity";

function EquityPanel() {
  const { data, error } = usePolling(api.overview, 30_000);
  if (error) return <DataUnavailable title={TITLE} error={error} />;
  const value = data?.snapshot ? `$${data.snapshot.equity.toLocaleString("en-US", { minimumFractionDigits: 2 })}` : "—";
  return <StatTile label={TITLE} value={value} />;
}

export const panel: PanelDefinition = {
  id: "equity", title: TITLE, defaultSize: { w: 3, h: 2 }, Component: EquityPanel,
};
