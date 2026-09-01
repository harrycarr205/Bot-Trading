import { useEffect, useRef, useState } from "react";

interface StatTileProps {
  label: string;
  value: string;
  tone?: "gain" | "loss" | "neutral";
}

export function StatTile({ label, value, tone = "neutral" }: StatTileProps) {
  const [flash, setFlash] = useState<"flash-gain" | "flash-loss" | "">("");
  const prevValue = useRef(value);

  useEffect(() => {
    if (prevValue.current !== value) {
      setFlash(tone === "loss" ? "flash-loss" : "flash-gain");
      prevValue.current = value;
      const id = setTimeout(() => setFlash(""), 400);
      return () => clearTimeout(id);
    }
  }, [value, tone]);

  return (
    <div className={flash} style={{ padding: 8 }}>
      <div className="label" style={{ fontSize: 11 }}>{label}</div>
      <div className={`stat-value ${tone !== "neutral" ? tone : ""}`} style={{ fontSize: 22 }}>{value}</div>
    </div>
  );
}
