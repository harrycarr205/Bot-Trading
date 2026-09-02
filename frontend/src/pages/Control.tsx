import { usePolling } from "../hooks/usePolling";
import { useMutation } from "../hooks/useMutation";
import { api } from "../api/client";
import { Card } from "../components/Card";
import { DataUnavailable } from "../components/DataUnavailable";
import { MutationStatus } from "../components/MutationStatus";
import type { ControlStartResult, ControlStopResult } from "../api/types";

type ControlAction = "start" | "stop" | "force-stop";
type ControlResult = ControlStartResult | ControlStopResult;

/**
 * The backend's `note` is the only place some operator-critical guidance lives —
 * most importantly the 60s stop timeout warning about a forced kill orphaning a
 * live order at the broker. It is always preferred over a generic summary.
 */
function describeControlResult(result: ControlResult): string {
  if (result.note) return result.note;
  if ("stopped" in result) {
    if (!result.stopped) return "Still running.";
    return result.forced ? "Force stopped." : "Stopped.";
  }
  return result.pid !== null ? `Started (PID ${result.pid}).` : "Started.";
}

/** A succeeded call whose result still needs attention reads in the loss colour. */
function resultNeedsAttention(result: ControlResult): boolean {
  return "stopped" in result ? !result.stopped : !result.started;
}

// Restored from the retired Jinja2 control.html: a successful stop within the
// timeout returns no `note`, so this guidance cannot live in the dynamic
// response alone — it has to be on the page before anything is clicked.
const STOP_SAFETY_COPY =
  "Stop waits up to 60s for a graceful exit (an in-flight research cycle finishes " +
  "its current ticker first). Force Stop kills immediately — only use it if Stop " +
  "has already timed out; a forced kill mid-order-submission can leave an order " +
  "live at the broker with no local record.";

function ProcessControls({
  name, alive, pid, log, onChanged,
}: {
  name: "scheduler" | "watchdog";
  alive: boolean;
  pid: number | null;
  log: string | null;
  onChanged: () => void;
}) {
  // One mutation per card rather than three, so the status line always reflects
  // the most recent action instead of leaving stale feedback from an earlier one.
  const action = useMutation<[ControlAction], ControlResult>(
    (kind) =>
      kind === "start"
        ? api.controlStart(name)
        : kind === "stop"
          ? api.controlStop(name)
          : api.controlForceStop(name),
    { onSuccess: onChanged }
  );

  return (
    <Card title={name === "scheduler" ? "Scheduler" : "Watchdog"}>
      <p>Status: <span className={alive ? "gain" : "loss"}>{alive ? "running" : "stopped"}</span>{alive && ` (PID ${pid})`}</p>
      <button disabled={alive || action.pending} onClick={() => action.run("start")}>Start</button>{" "}
      <button disabled={!alive || action.pending} onClick={() => action.run("stop")}>Stop</button>{" "}
      <button disabled={!alive || action.pending} onClick={() => action.run("force-stop")}>Force Stop</button>
      <p style={{ fontSize: 12, color: "var(--muted)", marginTop: 8 }}>{STOP_SAFETY_COPY}</p>
      <MutationStatus
        pending={action.pending}
        error={action.error}
        message={action.result ? describeControlResult(action.result) : null}
        tone={action.result && resultNeedsAttention(action.result) ? "warn" : "ok"}
      />
      <h3 style={{ fontSize: 12 }}>Log (last 200 lines)</h3>
      <pre className="mono" style={{ maxHeight: 200, overflow: "auto", fontSize: 11 }}>{log ?? "no log yet"}</pre>
    </Card>
  );
}

export default function Control() {
  const { data, error, refetch } = usePolling(api.controlStatus, 5_000);
  const runNow = useMutation(() => api.runNow(), { onSuccess: refetch });

  if (error) return <DataUnavailable error={error} />;
  if (!data) return <p style={{ color: "var(--muted)" }}>Loading…</p>;

  return (
    <div style={{ display: "grid", gap: 16 }}>
      <ProcessControls name="scheduler" alive={data.scheduler.alive} pid={data.scheduler.pid} log={data.scheduler_log} onChanged={refetch} />
      <ProcessControls name="watchdog" alive={data.watchdog.alive} pid={data.watchdog.pid} log={data.watchdog_log} onChanged={refetch} />
      <Card title="Off-cycle run">
        <p>Runs a single research/trade cycle right now, independent of the twice-daily schedule.</p>
        <button disabled={runNow.pending} onClick={() => runNow.run()}>Run now</button>
        <MutationStatus
          pending={runNow.pending}
          error={runNow.error}
          message={runNow.result ? describeControlResult(runNow.result) : null}
          tone={runNow.result && resultNeedsAttention(runNow.result) ? "warn" : "ok"}
        />
        <h3 style={{ fontSize: 12 }}>Log (last 200 lines)</h3>
        <pre className="mono" style={{ maxHeight: 200, overflow: "auto", fontSize: 11 }}>{data.run_once_log ?? "no log yet"}</pre>
      </Card>
    </div>
  );
}
