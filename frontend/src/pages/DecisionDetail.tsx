import { Link, useParams } from "react-router-dom";
import { usePolling } from "../hooks/usePolling";
import { api } from "../api/client";
import { Card } from "../components/Card";

export default function DecisionDetail() {
  const { id = "" } = useParams();
  const { data, error } = usePolling(() => api.decisionDetail(id), 0);

  if (error) return <div className="loss">Data unavailable — {error.message}</div>;
  if (!data) return null;

  return (
    <div style={{ display: "grid", gap: 16 }}>
      <div>
        <Link to="/decisions">&larr; back to decisions</Link>
        <h1><Link to={`/ticker/${data.ticker}`}>{data.ticker}</Link> — {data.run_type} — {new Date(data.started_at).toLocaleString()}</h1>
      </div>
      <Card>
        <p>Outcome: {data.outcome ?? "-"} | Market status: {data.market_status}</p>
        {data.decision ? (
          <>
            <p>Rating: {data.decision.rating} | Decision: {data.decision.decision}</p>
            <p>{data.decision.reasoning_summary}</p>
          </>
        ) : (
          <p>No decision recorded for this run.</p>
        )}
        {data.order_id && <p><Link to="/orders">View resulting order &rarr;</Link></p>}
      </Card>
      {data.transcripts.map((t) => (
        <Card key={t.role} title={t.role}>
          <pre style={{ whiteSpace: "pre-wrap", fontFamily: "var(--font-body)" }}>{t.content}</pre>
        </Card>
      ))}
    </div>
  );
}
