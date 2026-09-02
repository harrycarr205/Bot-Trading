import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { panel } from "./PositionsPanel";
import { api } from "../api/client";

vi.mock("../api/client", () => ({ api: { positions: vi.fn() } }));

function renderPanel() {
  return render(<MemoryRouter><panel.Component /></MemoryRouter>);
}

describe("PositionsPanel", () => {
  it("renders held positions", async () => {
    vi.mocked(api.positions).mockResolvedValue({
      positions: [{ ticker: "AAPL", qty: 10, avg_entry_price: 180, current_price: 190, unrealized_pnl: 100, latest_decision: "BUY", latest_rating: "4" }],
      candidates: [],
    });

    renderPanel();

    expect(await screen.findByText("AAPL")).toBeInTheDocument();
  });

  it("distinguishes a failed fetch from a genuinely empty portfolio", async () => {
    vi.mocked(api.positions).mockRejectedValue(new Error("500 Internal Server Error"));

    renderPanel();

    expect(await screen.findByText(/Data unavailable/)).toBeInTheDocument();
    expect(screen.queryByText("No open positions.")).not.toBeInTheDocument();
  });

  it("shows a loading line before the first fetch settles", () => {
    vi.mocked(api.positions).mockReturnValue(new Promise(() => {}));

    renderPanel();

    expect(screen.getByText("Loading…")).toBeInTheDocument();
    expect(screen.queryByText("No open positions.")).not.toBeInTheDocument();
  });
});
