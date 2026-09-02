import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { DashboardGrid, reconcileLayout } from "./DashboardGrid";
import { panelRegistry } from "../panels/registry";

describe("DashboardGrid", () => {
  it("renders every registered panel's title", () => {
    render(<DashboardGrid />);
    expect(screen.getByText("Equity")).toBeInTheDocument();
    expect(screen.getByText("Heartbeat")).toBeInTheDocument();
  });
});

describe("reconcileLayout", () => {
  it("gives a newly-registered panel its declared defaultSize, not a 1x1 default", () => {
    // A layout saved before "activity" was registered.
    const saved = panelRegistry
      .filter((p) => p.id !== "activity")
      .map((p, i) => ({ i: p.id, x: 0, y: i, w: p.defaultSize.w, h: p.defaultSize.h }));

    const reconciled = reconcileLayout(saved);

    const activity = panelRegistry.find((p) => p.id === "activity")!;
    const entry = reconciled.find((e) => e.i === "activity");
    expect(entry).toBeDefined();
    expect(entry!.w).toBe(activity.defaultSize.w);
    expect(entry!.h).toBe(activity.defaultSize.h);
  });

  it("covers every registered panel and preserves the saved geometry of existing ones", () => {
    const saved = [{ i: "equity", x: 9, y: 4, w: 3, h: 2 }];

    const reconciled = reconcileLayout(saved);

    expect(reconciled.map((e) => e.i).sort()).toEqual(panelRegistry.map((p) => p.id).sort());
    expect(reconciled.find((e) => e.i === "equity")).toEqual({ i: "equity", x: 9, y: 4, w: 3, h: 2 });
  });

  it("appends new panels below everything already placed, not on top of it", () => {
    const saved = [{ i: "equity", x: 0, y: 0, w: 3, h: 2 }];

    const reconciled = reconcileLayout(saved);

    for (const entry of reconciled.filter((e) => e.i !== "equity")) {
      expect(entry.y).toBeGreaterThanOrEqual(2);
    }
  });

  it("drops saved entries for panels no longer in the registry", () => {
    const saved = [
      { i: "equity", x: 0, y: 0, w: 3, h: 2 },
      { i: "retired-panel", x: 3, y: 0, w: 3, h: 2 },
    ];

    expect(reconcileLayout(saved).map((e) => e.i)).not.toContain("retired-panel");
  });
});
