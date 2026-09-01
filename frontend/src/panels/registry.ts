import type { PanelDefinition } from "./types";
import { panel as equityPanel } from "./EquityPanel";
import { panel as heartbeatPanel } from "./HeartbeatPanel";
import { panel as breakersPanel } from "./BreakersPanel";
import { panel as equityCurvePanel } from "./EquityCurvePanel";
import { panel as positionsPanel } from "./PositionsPanel";
import { panel as activityPanel } from "./ActivityPanel";

// Panels are registered explicitly here rather than via import.meta.glob:
// Vite's glob import requires statically-analyzable literal patterns, and an
// explicit list keeps the "add a panel" step (one import + one array entry)
// equally simple while staying type-checked end to end.
export const panelRegistry: PanelDefinition[] = [equityPanel, heartbeatPanel, breakersPanel, equityCurvePanel, positionsPanel, activityPanel];
