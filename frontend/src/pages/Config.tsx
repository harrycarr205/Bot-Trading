import { useEffect, useState } from "react";
import { usePolling } from "../hooks/usePolling";
import { useMutation } from "../hooks/useMutation";
import { api } from "../api/client";
import { Card } from "../components/Card";
import { DataUnavailable } from "../components/DataUnavailable";
import { MutationStatus } from "../components/MutationStatus";
import type { RiskConfigOut } from "../api/types";

// Mirrors _ENV_FIELDS / EnvSettingsIn in src/tradingsystem/dashboard/routes/config.py.
// The first two are ints server-side; the rest are strings.
const ENV_NUMERIC_FIELDS = ["discovery_slots_per_cycle", "watchdog_check_interval_minutes"] as const;
const ENV_TEXT_FIELDS = [
  "pre_market_cron",
  "midday_cron",
  "tradingagents_deep_think_model",
  "tradingagents_quick_think_model",
] as const;
const ENV_FIELDS = [...ENV_NUMERIC_FIELDS, ...ENV_TEXT_FIELDS];

type EnvDraft = Record<string, string>;

export default function Config() {
  const { data, error, refetch } = usePolling(api.config, 0);
  const [tickers, setTickers] = useState("");
  const [risk, setRisk] = useState<RiskConfigOut | null>(null);
  const [note, setNote] = useState("");
  // Held as strings so the inputs stay editable (an empty box is a valid
  // intermediate state); coerced to numbers only at save time.
  const [env, setEnv] = useState<EnvDraft>({});

  useEffect(() => {
    if (data) {
      setTickers(data.tickers.join("\n"));
      setRisk(data.risk_config);
      setEnv(Object.fromEntries(ENV_FIELDS.map((f) => [f, data.env_values[f] ?? ""])));
    }
  }, [data]);

  const saveCandidates = useMutation(
    () => api.saveCandidateUniverse(tickers.split("\n").map((t) => t.trim()).filter(Boolean)),
    { onSuccess: refetch }
  );

  const saveRisk = useMutation(async () => {
    // Thrown rather than a silent early return: routed through useMutation's
    // error channel it becomes visible feedback instead of a dead button.
    if (!note.trim()) throw new Error("A justification is required before saving risk config.");
    return api.saveRiskConfig(risk!, note);
  }, { onSuccess: refetch });

  const saveEnv = useMutation(
    () =>
      api.saveEnvSettings({
        discovery_slots_per_cycle: Number(env.discovery_slots_per_cycle),
        watchdog_check_interval_minutes: Number(env.watchdog_check_interval_minutes),
        pre_market_cron: env.pre_market_cron,
        midday_cron: env.midday_cron,
        tradingagents_deep_think_model: env.tradingagents_deep_think_model,
        tradingagents_quick_think_model: env.tradingagents_quick_think_model,
      }),
    { onSuccess: refetch }
  );

  if (error) return <DataUnavailable error={error} />;
  if (!data || !risk) return <p style={{ color: "var(--muted)" }}>Loading…</p>;

  return (
    <div style={{ display: "grid", gap: 16 }}>
      <Card title="Candidate universe">
        <p>Freely editable — add/remove tickers, takes effect next cycle.</p>
        <textarea rows={10} cols={20} value={tickers} onChange={(e) => setTickers(e.target.value)} />
        <br />
        <button disabled={saveCandidates.pending} onClick={() => saveCandidates.run()}>Save</button>
        <MutationStatus pending={saveCandidates.pending} error={saveCandidates.error} message={saveCandidates.result ? "Saved." : null} />
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
        <button disabled={saveRisk.pending} onClick={() => saveRisk.run()}>Save</button>
        <MutationStatus pending={saveRisk.pending} error={saveRisk.error} message={saveRisk.result ? "Saved — change appended to run/config_changes.log." : null} />
      </Card>
      <Card title="Env settings">
        <p>Written straight to <code>.env</code>. Cron changes take effect the next time the scheduler starts.</p>
        {ENV_NUMERIC_FIELDS.map((field) => (
          <label key={field} style={{ display: "block", marginBottom: 4 }}>
            {field}{" "}
            <input
              type="number" step="1" value={env[field] ?? ""}
              onChange={(e) => setEnv({ ...env, [field]: e.target.value })}
            />
          </label>
        ))}
        {ENV_TEXT_FIELDS.map((field) => (
          <label key={field} style={{ display: "block", marginBottom: 4 }}>
            {field}{" "}
            <input
              type="text" size={32} value={env[field] ?? ""}
              onChange={(e) => setEnv({ ...env, [field]: e.target.value })}
            />
          </label>
        ))}
        <button disabled={saveEnv.pending} onClick={() => saveEnv.run()}>Save</button>
        <MutationStatus pending={saveEnv.pending} error={saveEnv.error} message={saveEnv.result ? "Saved." : null} />
      </Card>
    </div>
  );
}
