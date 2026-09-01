import type { ComponentType } from "react";

export interface PanelDefinition {
  id: string;
  title: string;
  defaultSize: { w: number; h: number };
  Component: ComponentType;
}
