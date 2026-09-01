import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import TickerDetail from "./TickerDetail";
import { api } from "../api/client";

vi.mock("../api/client", () => ({ api: { ticker: vi.fn() } }));

describe("TickerDetail page", () => {
  it("shows decision history and orders for the routed symbol", async () => {
    vi.mocked(api.ticker).mockResolvedValue({
      ticker: "AAPL",
      runs: [{ id: "r1", ticker: "AAPL", run_type: "pre_market", started_at: "2026-08-24T09:00:00", outcome: "decision_recorded", decision: { id: "d1", rating: "Buy", decision: "buy", reasoning_summary: "strong" } }],
      orders: [{ id: "o1", submitted_at: "2026-08-24T09:05:00", ticker: "AAPL", side: "buy", qty: 10, limit_price: 180, status: "filled", decision_id: "d1", agent_run_id: "r1", fills: [], cancellable: false }],
    });

    render(
      <MemoryRouter initialEntries={["/ticker/AAPL"]}>
        <Routes><Route path="/ticker/:symbol" element={<TickerDetail />} /></Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByText("AAPL")).toBeInTheDocument();
    expect(api.ticker).toHaveBeenCalledWith("AAPL");
    expect(screen.getAllByText("buy").length).toBeGreaterThan(0);
  });
});
