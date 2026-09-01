import { useEffect, useState } from "react";
import { usePolling } from "../hooks/usePolling";
import { api } from "../api/client";
import { Card } from "../components/Card";
import type { RiskConfigOut } from "../api/types";

export default function Config() {
  const { data } = usePolling(api.config, 0);
  const [tickers, setTickers] = useState("");
  const [risk, setRisk] = useState<RiskConfigOut | null>(null);
  const [note, setNote] = useState("");

  useEffect(() => {
    if (data) {
      setTickers(data.tickers.join("\n"));
      setRisk(data.risk_config);
    }
  }, [data]);

  if (!data || !risk) return null;

  async function saveCandidates() {
    await api.saveCandidateUniverse(tickers.split("\n").map((t) => t.trim()).filter(Boolean));
  }

  async function saveRisk() {
    if (!note.trim()) return;
    await api.saveRiskConfig(risk!, note);
  }

  return (
    <div style={{ display: "grid", gap: 16 }}>
      <Card title="Candidate universe">
        <p>Freely editable — add/remove tickers, takes effect next cycle.</p>
        <textarea rows={10} cols={20} value={tickers} onChange={(e) => setTickers(e.target.value)} />
        <br />
        <button onClick={saveCandidates}>Save</button>
      </Card>
      <Card title="Risk config">
        <p>Changing these is a deliberate decision — a justification is required.</p>
        {(Object.keys(risk) as (keyof RiskConfigOut)[]).map((field) => (
          <label key={field} style={{ display: "block", marginBottom: 4 }}>
            {field}{" "}
            <input
              type="number" step="0.01" value={risk[field]}
              onChange={(e) => setRisk({ ...risk, [field]: Number(e.target.value) })}
            />
          </label>
        ))}
        <label>Justification (required)<br />
          <textarea rows={3} cols={40} value={note} onChange={(e) => setNote(e.target.value)} required />
        </label>
        <br />
        <button onClick={saveRisk}>Save</button>
      </Card>
    </div>
  );
}
