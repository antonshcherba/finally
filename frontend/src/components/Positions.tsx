import type { Position } from '@/lib/types';
import { pct, usd } from '@/lib/format';

export default function Positions({ positions }: { positions: Position[] }) {
  return (
    <table data-testid="positions-table" className="w-full text-right font-mono text-xs">
      <thead className="sticky top-0 bg-panel text-[10px] uppercase text-muted">
        <tr>
          {['Ticker', 'Qty', 'Avg Cost', 'Price', 'Value', 'P&L', '%'].map((h, i) => (
            <th key={h} className={`px-3 py-1.5 font-medium ${i === 0 ? 'text-left' : ''}`}>
              {h}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {positions.length === 0 && (
          <tr>
            <td colSpan={7} className="py-4 text-center font-sans text-muted">
              No open positions
            </td>
          </tr>
        )}
        {positions.map((p) => (
          <tr key={p.ticker} data-testid={`position-row-${p.ticker}`} className="border-t border-line/50">
            <td className="px-3 py-1 text-left font-sans font-semibold">{p.ticker}</td>
            <td className="px-3">{p.quantity}</td>
            <td className="px-3">{usd(p.avg_cost)}</td>
            <td className="px-3">{usd(p.current_price)}</td>
            <td className="px-3">{usd(p.market_value)}</td>
            <td className={`px-3 ${p.unrealized_pnl >= 0 ? 'text-up' : 'text-down'}`}>{usd(p.unrealized_pnl)}</td>
            <td className={`px-3 ${p.unrealized_pnl_pct >= 0 ? 'text-up' : 'text-down'}`}>{pct(p.unrealized_pnl_pct)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
