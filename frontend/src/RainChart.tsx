import { Droplets } from 'lucide-react';
import type { RainPoint } from './types';
import { date, number } from './format';

export default function RainChart({ data }: { data: RainPoint[] }) {
  const points = data.filter(d => d.rain_mm != null && Number.isFinite(d.rain_mm)).sort((a, b) => a.time.localeCompare(b.time));
  if (!points.length) return <div className="chart-empty"><Droplets size={21} /><span>Rainfall history unavailable for this cell.</span></div>;
  const width = 280, height = 106, top = 12, bottom = 25;
  const max = Math.max(...points.map(p => p.rain_mm!), 1);
  const chartHeight = height - top - bottom;
  const step = width / points.length;
  return <div className="rain-chart">
    <div className="chart-scale"><span>mm / interval</span><span>{number(max, 1)}</span></div>
    <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`Rainfall history from ${date(points[0].time)} to ${date(points[points.length - 1].time)}, maximum ${number(max, 1)} millimetres per interval`}>
      {[0, 0.5, 1].map(ratio => <line key={ratio} x1="0" x2={width} y1={top + chartHeight * ratio} y2={top + chartHeight * ratio} stroke="#e5e9e4" strokeDasharray="3 4" />)}
      {points.map((point, index) => <rect key={`${point.time}-${index}`} x={index * step + Math.min(2, step * 0.14)} y={top + chartHeight * (1 - point.rain_mm! / max)} width={Math.max(1, step - Math.min(4, step * 0.28))} height={Math.max(point.rain_mm === 0 ? 0 : 1, point.rain_mm! / max * chartHeight)} rx={Math.min(2, step / 4)} fill={index === points.length - 1 ? '#2d7063' : '#99c8bb'}><title>{date(point.time)}: {number(point.rain_mm, 1)} mm</title></rect>)}
      <text x="0" y={height - 5} fill="#7e8982" fontSize="10">{date(points[0].time, { year: undefined })}</text>
      <text x={width} y={height - 5} fill="#7e8982" fontSize="10" textAnchor="end">{date(points[points.length - 1].time, { year: undefined })}</text>
    </svg>
  </div>;
}
