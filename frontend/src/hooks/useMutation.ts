import { useCallback, useRef, useState } from "react";

interface MutationState<R> {
  /** True while the call is in flight — use it to disable the triggering button. */
  pending: boolean;
  /** Non-null once a call has failed (network error, non-2xx, or a client-side guard). */
  error: Error | null;
  /** The last successful response body, so callers can surface fields like `note`. */
  result: R | null;
}

interface MutationOptions<R> {
  /** Called after a successful run — typically a usePolling refetch. */
  onSuccess?: (result: R) => void;
}

export interface Mutation<A extends unknown[], R> extends MutationState<R> {
  run: (...args: A) => Promise<void>;
}

/**
 * The single mutation-handling mechanism for every write surface in the app.
 *
 * Every mutating action (order cancel, process start/stop/force-stop, off-cycle
 * run, config saves) goes through this rather than a floating `api.x()` promise,
 * so that a failure is never swallowed and a response body that carries operator
 * guidance (notably the Stop route's 60s-timeout `note`) is retained for display.
 *
 * A client-side precondition is expressed by throwing inside `fn` — it then
 * surfaces through the same `error` channel as a server-side 422, instead of
 * silently no-op'ing.
 */
export function useMutation<A extends unknown[], R>(
  fn: (...args: A) => Promise<R>,
  options: MutationOptions<R> = {}
): Mutation<A, R> {
  const [state, setState] = useState<MutationState<R>>({
    pending: false,
    error: null,
    result: null,
  });

  // Kept in refs so `run` stays referentially stable across renders even though
  // callers pass fresh closures (they capture component state) every render.
  const fnRef = useRef(fn);
  fnRef.current = fn;
  const onSuccessRef = useRef(options.onSuccess);
  onSuccessRef.current = options.onSuccess;

  const run = useCallback(async (...args: A) => {
    setState({ pending: true, error: null, result: null });
    try {
      const result = await fnRef.current(...args);
      setState({ pending: false, error: null, result });
      onSuccessRef.current?.(result);
    } catch (error) {
      setState({
        pending: false,
        error: error instanceof Error ? error : new Error(String(error)),
        result: null,
      });
    }
  }, []);

  return { ...state, run };
}
