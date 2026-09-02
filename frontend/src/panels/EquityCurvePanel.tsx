import { usePolling } from "../hooks/usePolling";
import { api } from "../api/client";
import { Chart } from "../components/Chart";
import { DataUnavailable } from "../components/DataUnavailable";
import type { PanelDefinition } from "./types";

const TITLE = "Equity curve";

function EquityCurvePanel() {
  const { data, error, loading } = usePolling(api.pnlSeries, 30_000);
  if (error) return <DataUnavailable title={TITLE} error={error} />;
  return (
    <div>
      <div className="label" style={{ fontSize: 11, marginBottom: 8 }}>{TITLE}</div>
      {!data || data.points.length === 0 ? (
        <div>{loading ? "Loading…" : "No portfolio snapshots recorded yet."}</div>
      ) : (
        <Chart points={data.points as any} xKey="date" yKey="equity" height={180} />
      )}
    </div>
  );
}

export const panel: PanelDefinition = {
  id: "equity-curve", title: TITLE, defaultSize: { w: 6, h: 3 }, Component: EquityCurvePanel,
};
