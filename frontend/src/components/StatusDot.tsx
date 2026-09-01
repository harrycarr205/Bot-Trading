export function StatusDot({ ok, label }: { ok: boolean; label: string }) {
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
      <span
        style={{
          width: 8, height: 8, borderRadius: "50%",
          background: ok ? "var(--gain)" : "var(--loss)",
          display: "inline-block",
        }}
      />
      <span>{label}</span>
    </span>
  );
}
