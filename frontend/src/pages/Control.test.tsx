import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import Control from "./Control";
import { api } from "../api/client";

vi.mock("../api/client", () => ({
  api: { controlStatus: vi.fn(), controlStart: vi.fn(), controlStop: vi.fn(), controlForceStop: vi.fn(), runNow: vi.fn() },
}));

function mockRunning() {
  vi.mocked(api.controlStatus).mockResolvedValue({
    scheduler: { alive: true, pid: 12345 }, watchdog: { alive: false, pid: null },
    scheduler_log: "log line", watchdog_log: null, run_once_log: null,
  });
}

describe("Control page", () => {
  it("disables Start when the scheduler is already running and calls stop on click", async () => {
    mockRunning();
    vi.mocked(api.controlStop).mockResolvedValue({ stopped: true, forced: false });

    render(<Control />);

    const startButtons = await screen.findAllByRole("button", { name: "Start" });
    expect(startButtons[0]).toBeDisabled();

    const stopButtons = screen.getAllByRole("button", { name: "Stop" });
    fireEvent.click(stopButtons[0]);

    await waitFor(() => expect(api.controlStop).toHaveBeenCalledWith("scheduler"));
    expect(await screen.findByText("Stopped.")).toBeInTheDocument();
  });

  it("displays the stop route's timeout note rather than discarding it", async () => {
    mockRunning();
    const note =
      "did not stop within 60s — it may still be finishing an in-flight research cycle. " +
      "Use Force Stop only if you're sure it's safe to kill (a forced kill mid-order-submission " +
      "can leave an order live at the broker with no local record).";
    vi.mocked(api.controlStop).mockResolvedValue({ stopped: false, forced: false, note });

    render(<Control />);

    fireEvent.click((await screen.findAllByRole("button", { name: "Stop" }))[0]);

    expect(await screen.findByText(note)).toBeInTheDocument();
  });

  it("surfaces a failed control action instead of swallowing it", async () => {
    mockRunning();
    vi.mocked(api.controlStart).mockRejectedValue(new Error("409: watchdog is already running"));

    render(<Control />);

    // The watchdog is stopped in this fixture, so its Start button is enabled.
    fireEvent.click((await screen.findAllByRole("button", { name: "Start" }))[1]);

    expect(await screen.findByText(/409: watchdog is already running/)).toBeInTheDocument();
  });

  it("carries the static Stop / Force Stop safety guidance as page copy", async () => {
    mockRunning();
    render(<Control />);

    const copy = await screen.findAllByText(/a forced kill mid-order-submission can leave an order live at the broker/);
    // One per process card (scheduler and watchdog).
    expect(copy).toHaveLength(2);
  });

  it("surfaces a failed off-cycle run", async () => {
    mockRunning();
    vi.mocked(api.runNow).mockRejectedValue(new Error("500: spawn failed"));

    render(<Control />);

    fireEvent.click(await screen.findByRole("button", { name: "Run now" }));

    expect(await screen.findByText(/500: spawn failed/)).toBeInTheDocument();
  });
});
