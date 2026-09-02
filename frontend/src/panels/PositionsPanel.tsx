import { Link } from "react-router-dom";
import { usePolling } from "../hooks/usePolling";
import { api } from "../api/client";
import { DataTable } from "../components/DataTable";
import { DataUnavailable } from "../components/DataUnavailable";
import type { PositionOut } from "../api/types";
import type { PanelDefinition } from "./types";

const TITLE = "Positions";

function PositionsPanel() {
  const { data, error, loading } = usePolling(api.positions, 30_000);
  // A down /api/positions previously rendered "No open positions." —
  // indistinguishable from a genuinely empty portfolio.
  if (error) return <DataUnavailable title={TITLE} error={error} />;
  return (
    <div>
      <div className="label" style={{ fontSize: 11, marginBottom: 8 }}>{TITLE}</div>
      <DataTable<PositionOut>
        columns={[
          { header: "Ticker", render: (p) => <Link to={`/ticker/${p.ticker}`}>{p.ticker}</Link> },
          { header: "Qty", render: (p) => p.qty },
          { header: "P&L", render: (p) => <span className={p.unrealized_pnl >= 0 ? "gain" : "loss"}>${p.unrealized_pnl.toFixed(2)}</span> },
          { header: "Decision", render: (p) => p.latest_decision ?? "-" },
        ]}
        rows={data?.positions ?? []}
        getRowKey={(p) => p.ticker}
        emptyMessage="No open positions."
        loading={loading}
      />
    </div>
  );
}

export const panel: PanelDefinition = {
  id: "positions", title: TITLE, defaultSize: { w: 6, h: 3 }, Component: PositionsPanel,
};
