import type { ReactNode } from "react";

export function Card({ title, children }: { title?: string; children: ReactNode }) {
  return (
    <div style={{ background: "var(--panel)", border: "1px solid var(--hairline)", borderRadius: "var(--radius)", padding: 16 }}>
      {title && <h2 style={{ fontSize: 13, margin: "0 0 12px" }}>{title}</h2>}
      {children}
    </div>
  );
}
