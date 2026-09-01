import { Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

interface ChartPoint { [key: string]: string | number; }

interface ChartProps {
  points: ChartPoint[];
  xKey: string;
  yKey: string;
  height?: number;
}

export function Chart({ points, xKey, yKey, height = 200 }: ChartProps) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={points}>
        <XAxis dataKey={xKey} stroke="var(--muted)" fontSize={11} />
        <YAxis stroke="var(--muted)" fontSize={11} domain={["auto", "auto"]} />
        <Tooltip contentStyle={{ background: "var(--panel)", border: "1px solid var(--hairline)" }} />
        <Line type="monotone" dataKey={yKey} stroke="var(--amber)" dot={false} strokeWidth={2} />
      </LineChart>
    </ResponsiveContainer>
  );
}
