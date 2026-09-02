import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";

function Boom(): JSX.Element {
  throw new Error("panel exploded");
}

vi.mock("./routes", () => ({
  routes: [
    { path: "/boom", navPath: "/boom", navLabel: "Boom", Component: Boom },
    { path: "/safe", navPath: "/safe", navLabel: "Safe", Component: () => <div>safe page</div> },
  ],
  navEntries: [
    { navPath: "/boom", navLabel: "Boom" },
    { navPath: "/safe", navLabel: "Safe" },
  ],
}));

vi.mock("./api/client", () => ({ api: { overview: vi.fn().mockResolvedValue(null) } }));

describe("App error boundary", () => {
  beforeEach(() => {
    // React logs the caught render error; silenced so the suite output stays readable.
    vi.spyOn(console, "error").mockImplementation(() => {});
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("clears the boundary on navigation instead of staying broken until a reload", () => {
    render(<MemoryRouter initialEntries={["/boom"]}><App /></MemoryRouter>);

    expect(screen.getByText(/Data unavailable/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("link", { name: "Safe" }));

    expect(screen.getByText("safe page")).toBeInTheDocument();
    expect(screen.queryByText(/Data unavailable/)).not.toBeInTheDocument();
  });
});
