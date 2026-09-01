import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import Positions from "./Positions";
import { api } from "../api/client";

vi.mock("../api/client", () => ({ api: { positions: vi.fn() } }));

describe("Positions page", () => {
  it("lists held positions and candidate tickers, linking to Ticker Detail", async () => {
    vi.mocked(api.positions).mockResolvedValue({
      positions: [{ ticker: "AAPL", qty: 10, avg_entry_price: 180, current_price: 184.5, unrealized_pnl: 45, latest_decision: "buy", latest_rating: "Buy" }],
      candidates: [{ ticker: "MSFT", held: false }],
    });

    render(<MemoryRouter><Positions /></MemoryRouter>);

    expect(await screen.findByRole("link", { name: "AAPL" })).toHaveAttribute("href", "/ticker/AAPL");
    expect(screen.getByText("MSFT")).toBeInTheDocument();
  });
});
