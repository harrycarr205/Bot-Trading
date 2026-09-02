interface MutationStatusProps {
  pending: boolean;
  error: Error | null;
  /** Message derived from a successful response (e.g. the Stop route's `note`). */
  message?: string | null;
  /** "warn" renders the message in the loss colour — for a succeeded call whose
   *  result still needs the operator's attention (e.g. "did not stop within 60s"). */
  tone?: "ok" | "warn";
}

const BASE_STYLE = { margin: "8px 0 0", fontSize: 12 } as const;

/** The single feedback surface for a useMutation call: in-flight, failed, or done. */
export function MutationStatus({ pending, error, message, tone = "ok" }: MutationStatusProps) {
  if (pending) {
    return <p role="status" style={{ ...BASE_STYLE, color: "var(--muted)" }}>Working…</p>;
  }
  if (error) {
    return <p role="alert" className="loss" style={BASE_STYLE}>Failed — {error.message}</p>;
  }
  if (message) {
    return (
      <p role="status" className={tone === "warn" ? "loss" : undefined} style={BASE_STYLE}>
        {message}
      </p>
    );
  }
  return null;
}
