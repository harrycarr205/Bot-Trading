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

/**
 * Makes a persisted layout current against the panel registry.
 *
 * This is what keeps the registry's "add a panel = one new file, nothing else
 * to touch" promise true for anyone who has ever dragged a panel. Without it,
 * a saved layout is returned verbatim, a newly-registered panel has no entry in
 * it, and react-grid-layout silently synthesizes a 1x1 default — ignoring the
 * panel's declared defaultSize entirely.
 *
 * Exported for direct unit testing: the resulting geometry is not meaningfully
 * assertable through react-grid-layout's rendered DOM.
 */
export function reconcileLayout(saved: Layout[]): Layout[] {
  const registryIds = new Set(panelRegistry.map((p) => p.id));
  // Entries for panels that have since been removed from the registry are dropped.
  const kept = saved.filter((entry) => registryIds.has(entry.i));
  const keptIds = new Set(kept.map((entry) => entry.i));
  const missing = panelRegistry.filter((p) => !keptIds.has(p.id));
  if (missing.length === 0) return kept;

  // x follows defaultLayout()'s registry-index formula and w/h come from
  // defaultSize, but a new panel is stacked below everything already placed
  // rather than at y=0 — so registering a panel never reshuffles a layout the
  // viewer has arranged by hand.
  let nextY = kept.reduce((max, entry) => Math.max(max, entry.y + entry.h), 0);
  const appended = missing.map((p) => {
    const entry: Layout = {
      i: p.id,
      x: (panelRegistry.indexOf(p) * p.defaultSize.w) % 12,
      y: nextY,
      w: p.defaultSize.w,
      h: p.defaultSize.h,
    };
    nextY += p.defaultSize.h;
    return entry;
  });
  return [...kept, ...appended];
}

function loadLayout(): Layout[] {
  try {
    const raw = localStorage.getItem(LAYOUT_KEY);
    if (raw) {
      const parsed = JSON.parse(raw);
      if (Array.isArray(parsed)) return reconcileLayout(parsed as Layout[]);
    }
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
        // Each panel renders its own title (via a module-level TITLE const shared with panel.title) —
        // the grid renders no chrome, to avoid double-rendering a panel's title alongside its own content (e.g. StatTile's label).
        <div key={p.id} className="panel-drag-handle" style={{ background: "var(--panel)", border: "1px solid var(--hairline)", borderRadius: "var(--radius)", padding: 12, overflow: "auto", cursor: "move" }}>
          <p.Component />
        </div>
      ))}
    </GridLayout>
  );
}
