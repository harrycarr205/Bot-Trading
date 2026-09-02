import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { useMutation } from "./useMutation";

describe("useMutation", () => {
  it("captures a rejection as error instead of leaving an unhandled promise", async () => {
    const fn = vi.fn().mockRejectedValue(new Error("422: justification is required"));

    const { result } = renderHook(() => useMutation(fn));
    await act(async () => {
      await result.current.run();
    });

    expect(result.current.error?.message).toBe("422: justification is required");
    expect(result.current.pending).toBe(false);
    expect(result.current.result).toBeNull();
  });

  it("keeps the response body so callers can surface fields like note", async () => {
    const body = { stopped: false, forced: false, note: "did not stop within 60s" };
    const { result } = renderHook(() => useMutation(() => Promise.resolve(body)));

    await act(async () => {
      await result.current.run();
    });

    expect(result.current.result).toEqual(body);
    expect(result.current.error).toBeNull();
  });

  it("calls onSuccess (the refetch hook) only after a successful run", async () => {
    const onSuccess = vi.fn();
    const fn = vi.fn().mockRejectedValueOnce(new Error("boom")).mockResolvedValueOnce({ ok: true });

    const { result } = renderHook(() => useMutation(fn, { onSuccess }));

    await act(async () => {
      await result.current.run();
    });
    expect(onSuccess).not.toHaveBeenCalled();

    await act(async () => {
      await result.current.run();
    });
    await waitFor(() => expect(onSuccess).toHaveBeenCalledWith({ ok: true }));
  });
});
