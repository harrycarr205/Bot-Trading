import { Link } from "react-router-dom";
import { usePolling } from "../hooks/usePolling";
import { api } from "../api/client";
import { DataTable } from "../components/DataTable";
import type { OrderOut } from "../api/types";

export default function Orders() {
  const { data, error } = usePolling(api.orders, 30_000);

  if (error) return <div className="loss">Data unavailable — {error.message}</div>;

  async function handleCancel(id: string) {
    await api.cancelOrder(id);
  }

  return (
    <div>
      <h1>Orders</h1>
      <DataTable<OrderOut>
        columns={[
          { header: "Submitted", render: (o) => new Date(o.submitted_at).toLocaleString() },
          { header: "Ticker", render: (o) => <Link to={`/ticker/${o.ticker}`}>{o.ticker}</Link> },
          { header: "Side", render: (o) => o.side },
          { header: "Qty", render: (o) => o.qty },
          { header: "Limit", render: (o) => `$${o.limit_price.toFixed(2)}` },
          { header: "Status", render: (o) => o.status },
          { header: "Decision", render: (o) => <Link to={`/decisions/${o.agent_run_id}`}>view</Link> },
          { header: "Fills", render: (o) => (o.fills.length === 0 ? "-" : o.fills.map((f) => `${f.fill_qty}@$${f.fill_price.toFixed(2)}`).join(", ")) },
          { header: "Cancel", render: (o) => (o.cancellable ? <button onClick={() => handleCancel(o.id)}>Cancel</button> : "-") },
        ]}
        rows={data?.orders ?? []}
        getRowKey={(o) => o.id}
        emptyMessage="No orders recorded yet."
      />
    </div>
  );
}
