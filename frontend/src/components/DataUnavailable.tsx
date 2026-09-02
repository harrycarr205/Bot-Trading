interface DataUnavailableProps {
  error: Error;
  /** Panels keep their own label above the message so the grid cell stays identifiable. */
  title?: string;
}

/**
 * The one "data unavailable" surface (spec §6: a broken or slow panel shows
 * "data unavailable", not a blank page and not an empty-state message that is
 * indistinguishable from genuinely-empty data).
 */
export function DataUnavailable({ error, title }: DataUnavailableProps) {
  return (
    <div>
      {title && <div className="label" style={{ fontSize: 11, marginBottom: 8 }}>{title}</div>}
      <div className="loss" role="alert">Data unavailable — {error.message}</div>
    </div>
  );
}
