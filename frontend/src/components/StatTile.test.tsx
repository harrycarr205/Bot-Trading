import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { StatTile } from "./StatTile";

describe("StatTile", () => {
  it("renders the label and value", () => {
    render(<StatTile label="Equity" value="$100,000.00" />);
    expect(screen.getByText("Equity")).toBeInTheDocument();
    expect(screen.getByText("$100,000.00")).toBeInTheDocument();
  });
});
