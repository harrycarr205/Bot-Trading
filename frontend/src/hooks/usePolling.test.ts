import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { usePolling } from "./usePolling";

describe("usePolling", () => {
  it("fetches immediately and re-fetches after intervalMs", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const fetcher = vi.fn().mockResolvedValue({ value: 1 });

    const { result } = renderHook(() => usePolling(fetcher, 1000));

    await waitFor(() => expect(result.current.data).toEqual({ value: 1 }));
    expect(fetcher).toHaveBeenCalledTimes(1);

    await act(async () => {
      vi.advanceTimersByTime(1000);
    });
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));

    vi.useRealTimers();
  });

  it("surfaces a fetch error without throwing", async () => {
    const fetcher = vi.fn().mockRejectedValue(new Error("network down"));

    const { result } = renderHook(() => usePolling(fetcher, 5000));

    await waitFor(() => expect(result.current.error?.message).toBe("network down"));
    expect(result.current.loading).toBe(false);
  });
});
