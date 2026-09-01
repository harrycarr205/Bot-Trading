import { usePolling } from "../hooks/usePolling";
import { api } from "../api/client";
import type { PanelDefinition } from "./types";

const TITLE = "Circuit breakers";

function BreakersPanel() {
  const { data } = usePolling(api.overview, 10_000);
  if (!data) return null;
  return (
    <div>
      <div className="label" style={{ fontSize: 11, marginBottom: 8 }}>{TITLE}</div>
      {data.active_breakers.length === 0 ? (
        <div className="gain">No active circuit breakers.</div>
      ) : (
        <ul style={{ margin: 0, paddingLeft: 16 }}>
          {data.active_breakers.map((b) => (
            <li key={b.id} className="loss">{b.breaker_type} — {b.trigger_reason}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

export const panel: PanelDefinition = {
  id: "breakers", title: TITLE, defaultSize: { w: 3, h: 2 }, Component: BreakersPanel,
};
