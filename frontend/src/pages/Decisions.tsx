import { useState } from "react";
import { Link } from "react-router-dom";
import { usePolling } from "../hooks/usePolling";
import { api } from "../api/client";
import { DataTable } from "../components/DataTable";
import type { AgentRunSummary } from "../api/types";

export default function Decisions() {
  const [ticker, setTicker] = useState("");
  const { data, error } = usePolling(() => api.decisions(ticker || undefined), 30_000);

  if (error) return <div className="loss">Data unavailable — {error.message}</div>;

  return (
    <div>
      <h1>Decisions / Journal</h1>
      <input
        placeholder="Filter by ticker"
        value={ticker}
        onChange={(e) => setTicker(e.target.value.toUpperCase())}
        style={{ marginBottom: 12, background: "var(--panel)", color: "var(--ink)", border: "1px solid var(--hairline)", padding: 6 }}
      />
      <DataTable<AgentRunSummary>
        columns={[
          { header: "Started", render: (r) => <Link to={`/decisions/${r.id}`}>{new Date(r.started_at).toLocaleString()}</Link> },
          { header: "Ticker", render: (r) => <Link to={`/ticker/${r.ticker}`}>{r.ticker}</Link> },
          { header: "Run type", render: (r) => r.run_type },
          { header: "Outcome", render: (r) => r.outcome ?? "-" },
          { header: "Rating", render: (r) => r.decision?.rating ?? "-" },
          { header: "Decision", render: (r) => r.decision?.decision ?? "-" },
        ]}
        rows={data?.runs ?? []}
        getRowKey={(r) => r.id}
        emptyMessage="No agent runs recorded yet."
      />
    </div>
  );
}
