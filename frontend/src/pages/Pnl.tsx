import { usePolling } from "../hooks/usePolling";
import { api } from "../api/client";
import { Card } from "../components/Card";
import { Chart } from "../components/Chart";
import { DataTable } from "../components/DataTable";
import { DataUnavailable } from "../components/DataUnavailable";
import type { OverviewSnapshot, RealizedPnlOut } from "../api/types";

export default function Pnl() {
  // Two independent polls: the tables below are fed by /api/pnl, the chart by
  // /api/pnl/series — each surface reports its own loading/error state rather
  // than borrowing the other's.
  const { data: pnl, error, loading } = usePolling(api.pnl, 30_000);
  const { data: series, error: seriesError, loading: seriesLoading } = usePolling(api.pnlSeries, 30_000);

  if (error) return <DataUnavailable error={error} />;

  return (
    <div style={{ display: "grid", gap: 16 }}>
      <Card title="Equity curve">
        {seriesError ? (
          <DataUnavailable error={seriesError} />
        ) : series && series.points.length > 0 ? (
          <Chart points={series.points as unknown as Array<{ [key: string]: string | number }>} xKey="date" yKey="equity" height={240} />
        ) : (
          <p>{seriesLoading ? "Loading…" : "No portfolio snapshots recorded yet."}</p>
        )}
      </Card>
      <Card title="Portfolio snapshots">
        <DataTable<OverviewSnapshot>
          columns={[
            { header: "Date", render: (s) => s.snapshot_date },
            { header: "Equity", render: (s) => `$${s.equity.toFixed(2)}` },
            { header: "Cash", render: (s) => `$${s.cash.toFixed(2)}` },
          ]}
          rows={pnl?.snapshots ?? []}
          getRowKey={(s) => s.snapshot_date}
          emptyMessage="No portfolio snapshots recorded yet."
          loading={loading}
        />
      </Card>
      <Card title="Realized P&L">
        <DataTable<RealizedPnlOut>
          columns={[
            { header: "Closed", render: (r) => new Date(r.closed_at).toLocaleString() },
            { header: "Ticker", render: (r) => r.ticker },
            { header: "Amount", render: (r) => <span className={r.pnl_amount >= 0 ? "gain" : "loss"}>{r.pnl_amount.toFixed(2)}</span> },
          ]}
          rows={pnl?.realized ?? []}
          getRowKey={(r) => r.id}
          emptyMessage="No realized P&L recorded yet."
          loading={loading}
        />
      </Card>
    </div>
  );
}
