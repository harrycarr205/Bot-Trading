import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import Control from "./Control";
import { api } from "../api/client";

vi.mock("../api/client", () => ({
  api: { controlStatus: vi.fn(), controlStart: vi.fn(), controlStop: vi.fn(), controlForceStop: vi.fn(), runNow: vi.fn() },
}));

describe("Control page", () => {
  it("disables Start when the scheduler is already running and calls stop on click", async () => {
    vi.mocked(api.controlStatus).mockResolvedValue({
      scheduler: { alive: true, pid: 12345 }, watchdog: { alive: false, pid: null },
      scheduler_log: "log line", watchdog_log: null, run_once_log: null,
    });
    vi.mocked(api.controlStop).mockResolvedValue({ stopped: true, forced: false });

    render(<Control />);

    const startButtons = await screen.findAllByRole("button", { name: "Start" });
    expect(startButtons[0]).toBeDisabled();

    const stopButtons = screen.getAllByRole("button", { name: "Stop" });
    fireEvent.click(stopButtons[0]);

    await waitFor(() => expect(api.controlStop).toHaveBeenCalledWith("scheduler"));
  });
});
