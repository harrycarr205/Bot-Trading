import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { DashboardGrid } from "./DashboardGrid";

describe("DashboardGrid", () => {
  it("renders every registered panel's title", () => {
    render(<DashboardGrid />);
    expect(screen.getByText("Equity")).toBeInTheDocument();
    expect(screen.getByText("Heartbeat")).toBeInTheDocument();
  });
});
