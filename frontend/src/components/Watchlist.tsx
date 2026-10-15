'use client';
import { useEffect, useRef, useState } from 'react';
import type { PriceUpdate } from '@/lib/types';
import type { HistoryPoint } from '@/lib/usePriceStream';
import { pct } from '@/lib/format';
import LineChart from './LineChart';

export function PriceCell({ ticker, price }: { ticker: string; price: number | undefined }) {
  const prev = useRef<number | undefined>(undefined);
  const [dir, setDir] = useState<'up' | 'down' | 'flat'>('flat');
  const [flash, setFlash] = useState('');

  useEffect(() => {
    const p = prev.current;
    prev.current = price;
    if (p === undefined || price === undefined || p === price) return;
    const d = price > p ? 'up' : 'down';
    setDir(d);
    setFlash(`flash-${d}`);
    const t = setTimeout(() => setFlash(''), 60);
    return () => clearTimeout(t);
  }, [price]);

  return (
    <span
      data-testid={`watchlist-price-${ticker}`}
      data-direction={dir}
      className={`flash-cell rounded px-1 font-mono ${flash}`}
    >
      {price === undefined ? '--' : price.toFixed(2)}
    </span>
  );
}

interface Props {
  tickers: string[];
  prices: Record<string, PriceUpdate>;
  history: Record<string, HistoryPoint[]>;
  selected: string | null;
  error: string;
  onSelect: (t: string) => void;
  onAdd: (t: string) => void;
  onRemove: (t: string) => void;
}

export default function Watchlist({ tickers, prices, history, selected, error, onSelect, onAdd, onRemove }: Props) {
  const [input, setInput] = useState('');

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!input.trim()) return;
    onAdd(input.trim().toUpperCase());
    setInput('');
  };

  return (
    <section className="flex h-full flex-col border-r border-line bg-panel">
      <h2 className="border-b border-line px-3 py-2 text-xs font-semibold uppercase text-muted">Watchlist</h2>
      <div className="flex-1 overflow-y-auto">
        {tickers.map((t) => {
          const p = prices[t];
          const change = p ? ((p.price - p.baseline_price) / p.baseline_price) * 100 : null;
          return (
            <div
              key={t}
              data-testid={`watchlist-row-${t}`}
              data-selected={selected === t}
              onClick={() => onSelect(t)}
              className={`group flex cursor-pointer items-center gap-2 border-b border-line/50 px-3 py-1.5 hover:bg-white/5 ${
                selected === t ? 'bg-primary/10' : ''
              }`}
            >
              <span className="w-12 font-semibold">{t}</span>
              <div className="h-8 w-16">
                <LineChart mini data={history[t] ?? []} color={change != null && change < 0 ? '#f85149' : '#3fb950'} />
              </div>
              <div className="ml-auto text-right">
                <PriceCell ticker={t} price={p?.price} />
                <div
                  data-testid={`watchlist-change-${t}`}
                  className={`font-mono text-[11px] ${change == null ? 'text-muted' : change >= 0 ? 'text-up' : 'text-down'}`}
                >
                  {pct(change)}
                </div>
              </div>
              <button
                data-testid={`watchlist-remove-${t}`}
                aria-label={`Remove ${t}`}
                onClick={(e) => {
                  e.stopPropagation();
                  onRemove(t);
                }}
                className="text-muted opacity-0 hover:text-down group-hover:opacity-100"
              >
                x
              </button>
            </div>
          );
        })}
      </div>
      <form onSubmit={submit} className="border-t border-line p-2">
        <div className="flex gap-1">
          <input
            data-testid="watchlist-add-input"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Add ticker"
            className="w-full rounded border border-line bg-bg px-2 py-1 uppercase outline-none focus:border-primary"
          />
          <button
            data-testid="watchlist-add-button"
            type="submit"
            className="rounded bg-secondary px-3 py-1 font-semibold text-white hover:opacity-90"
          >
            Add
          </button>
        </div>
        {error && (
          <div data-testid="watchlist-error" className="mt-1 text-xs text-down">
            {error}
          </div>
        )}
      </form>
    </section>
  );
}
