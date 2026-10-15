'use client';
import { ResponsiveContainer, Treemap } from 'recharts';
import type { Position } from '@/lib/types';

export function pnlColor(pnlPct: number) {
  const k = Math.min(Math.abs(pnlPct) / 10, 1);
  const alpha = 0.35 + 0.55 * k;
  return pnlPct >= 0 ? `rgba(63,185,80,${alpha})` : `rgba(248,81,73,${alpha})`;
}

function Cell(props: any) {
  const { x, y, width, height, name, pnl } = props;
  if (width == null || name == null) return <g />;
  return (
    <g>
      <rect x={x} y={y} width={width} height={height} fill={pnlColor(pnl ?? 0)} stroke="#0d1117" strokeWidth={2} />
      {width > 40 && height > 30 && (
        <text x={x + 6} y={y + 18} fill="#fff" fontSize={12} fontWeight={600}>
          {name} {(pnl ?? 0) >= 0 ? '+' : ''}
          {(pnl ?? 0).toFixed(1)}%
        </text>
      )}
    </g>
  );
}

export default function Heatmap({ positions }: { positions: Position[] }) {
  const data = positions.map((p) => ({ name: p.ticker, size: p.market_value, pnl: p.unrealized_pnl_pct }));
  return (
    <div data-testid="heatmap" className="h-full w-full">
      {data.length === 0 ? (
        <div className="flex h-full items-center justify-center text-muted">No positions</div>
      ) : (
        <ResponsiveContainer width="100%" height="100%">
          <Treemap data={data} dataKey="size" isAnimationActive={false} content={<Cell />} />
        </ResponsiveContainer>
      )}
    </div>
  );
}
