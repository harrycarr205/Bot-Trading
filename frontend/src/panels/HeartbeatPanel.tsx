import { usePolling } from "../hooks/usePolling";
import { api } from "../api/client";
import { StatusDot } from "../components/StatusDot";
import { DataUnavailable } from "../components/DataUnavailable";
import type { PanelDefinition } from "./types";

const TITLE = "Heartbeat";

function HeartbeatPanel() {
  const { data, error } = usePolling(api.overview, 10_000);
  // Without this, a down /api/overview reads as "No heartbeat recorded yet." —
  // indistinguishable from a scheduler that has genuinely never run.
  if (error) return <DataUnavailable title={TITLE} error={error} />;
  return (
    <div>
      <div className="label" style={{ fontSize: 11, marginBottom: 8 }}>{TITLE}</div>
      {!data?.heartbeat ? (
        <div>No heartbeat recorded yet.</div>
      ) : (
        <div>
          <StatusDot ok={!data.heartbeat_stale} label={data.heartbeat_stale ? "STALE" : "FRESH"} />
          <div className="mono" style={{ marginTop: 8, fontSize: 12, color: "var(--muted)" }}>
            {data.heartbeat.last_run_type} · {data.heartbeat.last_ticker ?? "-"}
          </div>
        </div>
      )}
    </div>
  );
}

export const panel: PanelDefinition = {
  id: "heartbeat", title: TITLE, defaultSize: { w: 3, h: 2 }, Component: HeartbeatPanel,
};
