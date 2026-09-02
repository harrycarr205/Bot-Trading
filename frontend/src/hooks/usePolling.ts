import { useCallback, useEffect, useRef, useState } from "react";

interface PollingState<T> {
  data: T | null;
  error: Error | null;
  loading: boolean;
}

export interface Polling<T> extends PollingState<T> {
  /** Re-runs the fetch immediately (and restarts the interval from now). */
  refetch: () => void;
}

export function usePolling<T>(
  fetcher: () => Promise<T>,
  intervalMs: number
): Polling<T> {
  const [state, setState] = useState<PollingState<T>>({
    data: null,
    error: null,
    loading: true,
  });
  // Bumped by refetch(); part of the effect's deps so a mutation can pull fresh
  // data straight away instead of waiting out the remaining poll interval
  // (and so a non-polling view, intervalMs === 0, can refresh at all).
  const [reloadToken, setReloadToken] = useState(0);
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;

    async function tick() {
      try {
        const data = await fetcherRef.current();
        if (!cancelled) setState({ data, error: null, loading: false });
      } catch (error) {
        if (!cancelled)
          setState((prev) => ({
            ...prev,
            error: error as Error,
            loading: false,
          }));
      } finally {
        if (!cancelled && intervalMs > 0) timer = setTimeout(tick, intervalMs);
      }
    }

    tick();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [intervalMs, reloadToken]);

  const refetch = useCallback(() => setReloadToken((n) => n + 1), []);

  return { ...state, refetch };
}
