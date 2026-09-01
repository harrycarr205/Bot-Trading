import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { DataTable } from "./DataTable";

interface Row { id: string; ticker: string; qty: number; }

describe("DataTable", () => {
  it("renders one row per item with the given columns", () => {
    const rows: Row[] = [{ id: "1", ticker: "AAPL", qty: 10 }];
    render(
      <DataTable<Row>
        columns={[{ header: "Ticker", render: (r) => r.ticker }, { header: "Qty", render: (r) => r.qty }]}
        rows={rows}
        getRowKey={(r) => r.id}
        emptyMessage="No rows"
      />,
    );
    expect(screen.getByText("AAPL")).toBeInTheDocument();
  });

  it("shows the empty message when there are no rows", () => {
    render(
      <DataTable<Row>
        columns={[{ header: "Ticker", render: (r) => r.ticker }]}
        rows={[]}
        getRowKey={(r) => r.id}
        emptyMessage="No rows yet"
      />,
    );
    expect(screen.getByText("No rows yet")).toBeInTheDocument();
  });
});
