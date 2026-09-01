import { usePolling } from "../hooks/usePolling";
import { api } from "../api/client";
import { Card } from "../components/Card";

function ProcessControls({ name, alive, pid, log }: { name: "scheduler" | "watchdog"; alive: boolean; pid: number | null; log: string | null }) {
  return (
    <Card title={name === "scheduler" ? "Scheduler" : "Watchdog"}>
      <p>Status: <span className={alive ? "gain" : "loss"}>{alive ? "running" : "stopped"}</span>{alive && ` (PID ${pid})`}</p>
      <button disabled={alive} onClick={() => api.controlStart(name)}>Start</button>{" "}
      <button disabled={!alive} onClick={() => api.controlStop(name)}>Stop</button>{" "}
      <button disabled={!alive} onClick={() => api.controlForceStop(name)}>Force Stop</button>
      <h3 style={{ fontSize: 12 }}>Log (last 200 lines)</h3>
      <pre className="mono" style={{ maxHeight: 200, overflow: "auto", fontSize: 11 }}>{log ?? "no log yet"}</pre>
    </Card>
  );
}

export default function Control() {
  const { data, error } = usePolling(api.controlStatus, 5_000);

  if (error) return <div className="loss">Data unavailable — {error.message}</div>;
  if (!data) return null;

  return (
    <div style={{ display: "grid", gap: 16 }}>
      <ProcessControls name="scheduler" alive={data.scheduler.alive} pid={data.scheduler.pid} log={data.scheduler_log} />
      <ProcessControls name="watchdog" alive={data.watchdog.alive} pid={data.watchdog.pid} log={data.watchdog_log} />
      <Card title="Off-cycle run">
        <button onClick={() => api.runNow()}>Run now</button>
        <h3 style={{ fontSize: 12 }}>Log (last 200 lines)</h3>
        <pre className="mono" style={{ maxHeight: 200, overflow: "auto", fontSize: 11 }}>{data.run_once_log ?? "no log yet"}</pre>
      </Card>
    </div>
  );
}
