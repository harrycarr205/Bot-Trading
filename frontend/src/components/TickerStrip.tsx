import { usePolling } from "../hooks/usePolling";
import { api } from "../api/client";
import { StatusDot } from "./StatusDot";

const money = (n: number) => `$${n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

export function TickerStrip() {
  const { data, error } = usePolling(api.overview, 10_000);

  return (
    <div style={{ display: "flex", gap: 24, alignItems: "center", padding: "8px 24px", borderBottom: "1px solid var(--hairline)", fontFamily: "var(--font-mono)" }}>
      <strong className="label">Bot-Trading Terminal</strong>
      {/* The strip is pinned to every page, so silently rendering nothing on a
          failed fetch would read as "no equity, no breakers" everywhere. */}
      {error && <span className="loss" role="alert">DATA UNAVAILABLE</span>}
      {data?.snapshot && <span>EQUITY {money(data.snapshot.equity)}</span>}
      {data && <StatusDot ok={!data.heartbeat_stale} label={data.heartbeat_stale ? "STALE" : "LIVE"} />}
      {data && data.active_breakers.length > 0 && (
        <span className="loss">{data.active_breakers.length} ACTIVE BREAKER{data.active_breakers.length > 1 ? "S" : ""}</span>
      )}
    </div>
  );
}
