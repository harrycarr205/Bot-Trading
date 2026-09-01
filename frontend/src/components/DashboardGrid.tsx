import GridLayout, { type Layout } from "react-grid-layout";
import { useEffect, useState } from "react";
import { panelRegistry } from "../panels/registry";
import "react-grid-layout/css/styles.css";

const LAYOUT_KEY = "dashboard.overview.layout.v1";

function defaultLayout(): Layout[] {
  return panelRegistry.map((p, i) => ({
    i: p.id, x: (i * p.defaultSize.w) % 12, y: 0, w: p.defaultSize.w, h: p.defaultSize.h,
  }));
}

function loadLayout(): Layout[] {
  try {
    const raw = localStorage.getItem(LAYOUT_KEY);
    if (raw) return JSON.parse(raw) as Layout[];
  } catch {
    // localStorage unavailable or corrupt — fall through to defaults
  }
  return defaultLayout();
}

export function DashboardGrid() {
  const [layout, setLayout] = useState<Layout[]>(loadLayout);

  useEffect(() => {
    try {
      localStorage.setItem(LAYOUT_KEY, JSON.stringify(layout));
    } catch {
      // best-effort persistence only
    }
  }, [layout]);

  return (
    <GridLayout
      className="dashboard-grid"
      cols={12}
      rowHeight={80}
      width={1200}
      layout={layout}
      onLayoutChange={setLayout}
      draggableHandle=".panel-drag-handle"
    >
      {panelRegistry.map((p) => (
        <div key={p.id} className="panel-drag-handle" style={{ background: "var(--panel)", border: "1px solid var(--hairline)", borderRadius: "var(--radius)", padding: 12, overflow: "auto", cursor: "move" }}>
          <p.Component />
        </div>
      ))}
    </GridLayout>
  );
}
