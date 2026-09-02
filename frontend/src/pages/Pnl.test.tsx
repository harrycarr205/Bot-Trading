import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import Pnl from "./Pnl";
import { api } from "../api/client";

vi.mock("../api/client", () => ({ api: { pnl: vi.fn(), pnlSeries: vi.fn() } }));

function mockPnl(decision_ids: string[]) {
  vi.mocked(api.pnl).mockResolvedValue({
    snapshots: [{ snapshot_date: "2026-08-24", equity: 100000, cash: 80000 }],
    realized: [{ id: "p1", ticker: "AAPL", pnl_amount: 250, closed_at: "2026-08-24T15:00:00", decision_ids, agent_run_ids: decision_ids.map((d) => `run-${d}`) }],
  });
  vi.mocked(api.pnlSeries).mockResolvedValue({ points: [{ date: "2026-08-24", equity: 100000 }] });
}

function renderPnl() {
  return render(<MemoryRouter><Pnl /></MemoryRouter>);
}

describe("Pnl page", () => {
  it("shows realized P&L rows and renders the equity curve chart container", async () => {
    mockPnl(["d1"]);

    renderPnl();

    expect(await screen.findByText("250.00")).toBeInTheDocument();
  });

  it("cross-links a realized row to its ticker and its originating decision", async () => {
    mockPnl(["d1", "d2"]);

    renderPnl();

    expect(await screen.findByRole("link", { name: "AAPL" })).toHaveAttribute("href", "/ticker/AAPL");
    // The agent-run id, not the decision id: /decisions/:id is keyed by run.
    const closedCell = screen.getByRole("link", { name: /2026/ });
    expect(closedCell).toHaveAttribute("href", "/decisions/run-d1");
  });

  it("leaves the Closed cell as plain text when a row resolved no agent runs", async () => {
    mockPnl([]);

    renderPnl();

    await screen.findByRole("link", { name: "AAPL" });
    expect(screen.queryByRole("link", { name: /2026/ })).not.toBeInTheDocument();
  });
});
