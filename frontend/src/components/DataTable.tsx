interface Column<T> {
  header: string;
  render: (row: T) => React.ReactNode;
}

interface DataTableProps<T> {
  columns: Column<T>[];
  rows: T[];
  getRowKey: (row: T) => string;
  emptyMessage: string;
}

export function DataTable<T>({ columns, rows, getRowKey, emptyMessage }: DataTableProps<T>) {
  return (
    <table style={{ width: "100%", borderCollapse: "collapse" }}>
      <thead>
        <tr>
          {columns.map((c) => (
            <th key={c.header} style={{ textAlign: "left", padding: "6px 8px", borderBottom: "1px solid var(--hairline)", color: "var(--muted)", fontSize: 11, textTransform: "uppercase" }}>
              {c.header}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.length === 0 ? (
          <tr><td colSpan={columns.length} style={{ padding: 12, color: "var(--muted)" }}>{emptyMessage}</td></tr>
        ) : (
          rows.map((row) => (
            <tr key={getRowKey(row)}>
              {columns.map((c) => (
                <td key={c.header} style={{ padding: "6px 8px", borderBottom: "1px solid var(--hairline)" }}>{c.render(row)}</td>
              ))}
            </tr>
          ))
        )}
      </tbody>
    </table>
  );
}
