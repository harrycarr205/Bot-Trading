import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import Orders from "./Orders";
import { api } from "../api/client";

vi.mock("../api/client", () => ({ api: { orders: vi.fn(), cancelOrder: vi.fn() } }));

describe("Orders page", () => {
  it("shows a Cancel button only for cancellable orders and calls the API on click", async () => {
    vi.mocked(api.orders).mockResolvedValue({
      orders: [{ id: "o1", submitted_at: "2026-08-24T09:00:00", ticker: "AAPL", side: "buy", qty: 10, limit_price: 180, status: "new", decision_id: "d1", agent_run_id: "r1", fills: [], cancellable: true }],
    });
    vi.mocked(api.cancelOrder).mockResolvedValue({ cancelled: true });

    render(<MemoryRouter><Orders /></MemoryRouter>);

    const button = await screen.findByRole("button", { name: /cancel/i });
    fireEvent.click(button);

    await waitFor(() => expect(api.cancelOrder).toHaveBeenCalledWith("o1"));
  });
});
