import { Link } from "react-router-dom";
import { usePolling } from "../hooks/usePolling";
import { useMutation } from "../hooks/useMutation";
import { api } from "../api/client";
import { DataTable } from "../components/DataTable";
import { DataUnavailable } from "../components/DataUnavailable";
import { MutationStatus } from "../components/MutationStatus";
import type { OrderOut } from "../api/types";

export default function Orders() {
  const { data, error, loading, refetch } = usePolling(api.orders, 30_000);
  // Refetches on success so the cancelled order's status updates immediately
  // rather than after up to 30s of polling.
  const cancel = useMutation((id: string) => api.cancelOrder(id), { onSuccess: refetch });

  if (error) return <DataUnavailable error={error} />;

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
          { header: "Cancel", render: (o) => (o.cancellable ? <button disabled={cancel.pending} onClick={() => cancel.run(o.id)}>Cancel</button> : "-") },
        ]}
        rows={data?.orders ?? []}
        getRowKey={(o) => o.id}
        emptyMessage="No orders recorded yet."
        loading={loading}
      />
      <MutationStatus
        pending={cancel.pending}
        error={cancel.error}
        message={cancel.result ? (cancel.result.cancelled ? "Order cancelled." : "Broker did not cancel the order.") : null}
        tone={cancel.result && !cancel.result.cancelled ? "warn" : "ok"}
      />
    </div>
  );
}
