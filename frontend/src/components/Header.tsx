'use client';
import type { ConnectionStatus } from '@/lib/types';
import { usd } from '@/lib/format';

const dot: Record<ConnectionStatus, string> = {
  connected: 'bg-up',
  reconnecting: 'bg-accent',
  disconnected: 'bg-down',
};

interface Props {
  totalValue: number | null;
  cash: number | null;
  status: ConnectionStatus;
  onReset: () => void;
}

export default function Header({ totalValue, cash, status, onReset }: Props) {
  return (
    <header className="flex items-center justify-between border-b border-line bg-panel px-4 py-2">
      <div className="flex items-center gap-3">
        <span className="text-lg font-bold tracking-wide text-accent">FinAlly</span>
        <span className="text-xs text-muted">AI Trading Workstation</span>
      </div>
      <div className="flex items-center gap-6">
        <div>
          <div className="text-[10px] uppercase text-muted">Portfolio</div>
          <div data-testid="header-total-value" className="font-mono text-base font-semibold">
            {usd(totalValue)}
          </div>
        </div>
        <div>
          <div className="text-[10px] uppercase text-muted">Cash</div>
          <div data-testid="header-cash" className="font-mono text-base">
            {usd(cash)}
          </div>
        </div>
        <div className="flex items-center gap-2" title={status}>
          <span data-testid="connection-status" data-status={status} className={`h-2.5 w-2.5 rounded-full ${dot[status]}`} />
          <span className="text-xs capitalize text-muted">{status}</span>
        </div>
        <button
          data-testid="reset-button"
          onClick={onReset}
          className="rounded border border-line px-3 py-1 text-xs text-muted hover:border-down hover:text-down"
        >
          Reset
        </button>
      </div>
    </header>
  );
}
