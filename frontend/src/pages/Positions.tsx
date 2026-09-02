import { Link } from "react-router-dom";
import { usePolling } from "../hooks/usePolling";
import { api } from "../api/client";
import { Card } from "../components/Card";
import { DataTable } from "../components/DataTable";
import { DataUnavailable } from "../components/DataUnavailable";
import type { CandidateOut, PositionOut } from "../api/types";

export default function Positions() {
  const { data, error, loading } = usePolling(api.positions, 30_000);

  if (error) return <DataUnavailable error={error} />;

  return (
    <div style={{ display: "grid", gap: 16 }}>
      <Card title="Held positions">
        <DataTable<PositionOut>
          columns={[
            { header: "Ticker", render: (p) => <Link to={`/ticker/${p.ticker}`}>{p.ticker}</Link> },
            { header: "Qty", render: (p) => p.qty },
            { header: "Avg entry", render: (p) => `$${p.avg_entry_price.toFixed(2)}` },
            { header: "Last", render: (p) => `$${p.current_price.toFixed(2)}` },
            { header: "P&L", render: (p) => <span className={p.unrealized_pnl >= 0 ? "gain" : "loss"}>${p.unrealized_pnl.toFixed(2)}</span> },
            { header: "Decision", render: (p) => p.latest_decision ?? "-" },
          ]}
          rows={data?.positions ?? []}
          getRowKey={(p) => p.ticker}
          emptyMessage="No open positions."
          loading={loading}
        />
      </Card>
      <Card title="Candidate universe">
        <DataTable<CandidateOut>
          columns={[
            { header: "Ticker", render: (c) => <Link to={`/ticker/${c.ticker}`}>{c.ticker}</Link> },
            { header: "Held", render: (c) => (c.held ? "yes" : "no") },
          ]}
          rows={data?.candidates ?? []}
          getRowKey={(c) => c.ticker}
          emptyMessage="No candidate tickers configured."
          loading={loading}
        />
      </Card>
    </div>
  );
}
