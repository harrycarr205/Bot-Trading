import { usePolling } from "../hooks/usePolling";
import { api } from "../api/client";
import { Chart } from "../components/Chart";
import type { PanelDefinition } from "./types";

const TITLE = "Equity curve";

function EquityCurvePanel() {
  const { data } = usePolling(api.pnlSeries, 30_000);
  if (!data || data.points.length === 0) return null;
  return (
    <div>
      <div className="label" style={{ fontSize: 11, marginBottom: 8 }}>{TITLE}</div>
      <Chart points={data.points as any} xKey="date" yKey="equity" height={180} />
    </div>
  );
}

export const panel: PanelDefinition = {
  id: "equity-curve", title: TITLE, defaultSize: { w: 6, h: 3 }, Component: EquityCurvePanel,
};
