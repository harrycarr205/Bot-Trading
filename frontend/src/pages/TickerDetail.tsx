import { Link, useParams } from "react-router-dom";
import { usePolling } from "../hooks/usePolling";
import { api } from "../api/client";
import { Card } from "../components/Card";
import { DataTable } from "../components/DataTable";
import type { AgentRunSummary, OrderOut } from "../api/types";

export default function TickerDetail() {
  const { symbol = "" } = useParams();
  const { data, error } = usePolling(() => api.ticker(symbol), 30_000);

  if (error) return <div className="loss">Data unavailable — {error.message}</div>;

  const latest = data?.runs[0];

  return (
    <div style={{ display: "grid", gap: 16 }}>
      <h1>{symbol}</h1>
      {latest?.decision && (
        <Card title="Latest reasoning">
          <p>Rating: {latest.decision.rating} | Decision: {latest.decision.decision}</p>
          <p>{latest.decision.reasoning_summary}</p>
        </Card>
      )}
      <Card title="Decision history">
        <DataTable<AgentRunSummary>
          columns={[
            { header: "Started", render: (r) => <Link to={`/decisions/${r.id}`}>{new Date(r.started_at).toLocaleString()}</Link> },
            { header: "Run type", render: (r) => r.run_type },
            { header: "Outcome", render: (r) => r.outcome ?? "-" },
            { header: "Decision", render: (r) => r.decision?.decision ?? "-" },
          ]}
          rows={data?.runs ?? []}
          getRowKey={(r) => r.id}
          emptyMessage="No decisions recorded for this ticker yet."
        />
      </Card>
      <Card title="Orders">
        <DataTable<OrderOut>
          columns={[
            { header: "Submitted", render: (o) => new Date(o.submitted_at).toLocaleString() },
            { header: "Side", render: (o) => o.side },
            { header: "Qty", render: (o) => o.qty },
            { header: "Status", render: (o) => o.status },
            { header: "Decision", render: (o) => <Link to={`/decisions/${o.agent_run_id}`}>view</Link> },
          ]}
          rows={data?.orders ?? []}
          getRowKey={(o) => o.id}
          emptyMessage="No orders recorded for this ticker yet."
        />
      </Card>
    </div>
  );
}
