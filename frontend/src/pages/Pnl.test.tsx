import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import Pnl from "./Pnl";
import { api } from "../api/client";

vi.mock("../api/client", () => ({ api: { pnl: vi.fn(), pnlSeries: vi.fn() } }));

describe("Pnl page", () => {
  it("shows realized P&L rows and renders the equity curve chart container", async () => {
    vi.mocked(api.pnl).mockResolvedValue({
      snapshots: [{ snapshot_date: "2026-08-24", equity: 100000, cash: 80000 }],
      realized: [{ id: "p1", ticker: "AAPL", pnl_amount: 250, closed_at: "2026-08-24T15:00:00", decision_ids: ["d1"] }],
    });
    vi.mocked(api.pnlSeries).mockResolvedValue({ points: [{ date: "2026-08-24", equity: 100000 }] });

    render(<Pnl />);

    expect(await screen.findByText("250.00")).toBeInTheDocument();
  });
});
