'use client';
import { useEffect, useState } from 'react';

interface Props {
  selected: string | null;
  error: string;
  onTrade: (ticker: string, quantity: number, side: 'buy' | 'sell') => void;
}

export default function TradeBar({ selected, error, onTrade }: Props) {
  const [ticker, setTicker] = useState('');
  const [qty, setQty] = useState('');

  useEffect(() => {
    if (selected) setTicker(selected);
  }, [selected]);

  const go = (side: 'buy' | 'sell') => {
    const q = Number(qty);
    if (!ticker.trim()) return;
    onTrade(ticker.trim().toUpperCase(), q, side);
  };

  const field = 'rounded border border-line bg-bg px-2 py-1 outline-none focus:border-primary';
  return (
    <div className="flex items-center gap-2 border-t border-line bg-panel px-3 py-2">
      <input
        data-testid="trade-ticker"
        value={ticker}
        onChange={(e) => setTicker(e.target.value)}
        placeholder="Ticker"
        className={`${field} w-24 uppercase`}
      />
      <input
        data-testid="trade-quantity"
        value={qty}
        onChange={(e) => setQty(e.target.value)}
        placeholder="Quantity"
        inputMode="decimal"
        className={`${field} w-28`}
      />
      <button
        data-testid="trade-buy"
        onClick={() => go('buy')}
        className="rounded bg-secondary px-4 py-1 font-semibold text-white hover:opacity-90"
      >
        Buy
      </button>
      <button
        data-testid="trade-sell"
        onClick={() => go('sell')}
        className="rounded bg-secondary px-4 py-1 font-semibold text-white hover:opacity-90"
      >
        Sell
      </button>
      {error && (
        <span data-testid="trade-error" className="text-xs text-down">
          {error}
        </span>
      )}
    </div>
  );
}
