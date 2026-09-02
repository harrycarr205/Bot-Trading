import { Link } from "react-router-dom";
import { usePolling } from "../hooks/usePolling";
import { api } from "../api/client";
import { DataTable } from "../components/DataTable";
import { DataUnavailable } from "../components/DataUnavailable";
import type { AgentRunSummary } from "../api/types";
import type { PanelDefinition } from "./types";

const TITLE = "Recent activity";

function ActivityPanel() {
  const { data, error, loading } = usePolling(() => api.decisions(), 30_000);
  if (error) return <DataUnavailable title={TITLE} error={error} />;
  const recent = (data?.runs ?? []).slice(0, 8);
  return (
    <div>
      <div className="label" style={{ fontSize: 11, marginBottom: 8 }}>{TITLE}</div>
      <DataTable<AgentRunSummary>
        columns={[
          { header: "When", render: (r) => <Link to={`/decisions/${r.id}`}>{new Date(r.started_at).toLocaleString()}</Link> },
          { header: "Ticker", render: (r) => <Link to={`/ticker/${r.ticker}`}>{r.ticker}</Link> },
          { header: "Outcome", render: (r) => r.decision?.decision ?? r.outcome ?? "-" },
        ]}
        rows={recent}
        getRowKey={(r) => r.id}
        emptyMessage="No activity recorded yet."
        loading={loading}
      />
    </div>
  );
}

export const panel: PanelDefinition = {
  id: "activity", title: TITLE, defaultSize: { w: 6, h: 3 }, Component: ActivityPanel,
};
