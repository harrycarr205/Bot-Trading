import type { PanelDefinition } from "./types";
import { panel as equityPanel } from "./EquityPanel";
import { panel as heartbeatPanel } from "./HeartbeatPanel";

// Panels are registered explicitly here rather than via import.meta.glob:
// Vite's glob import requires statically-analyzable literal patterns, and an
// explicit list keeps the "add a panel" step (one import + one array entry)
// equally simple while staying type-checked end to end.
export const panelRegistry: PanelDefinition[] = [equityPanel, heartbeatPanel];
