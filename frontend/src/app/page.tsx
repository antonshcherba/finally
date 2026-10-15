'use client';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { api, ApiError } from '@/lib/api';
import type { ChatMessage, Portfolio, Snapshot } from '@/lib/types';
import { usePriceStream } from '@/lib/usePriceStream';
import Header from '@/components/Header';
import Watchlist from '@/components/Watchlist';
import LineChart from '@/components/LineChart';
import Heatmap from '@/components/Heatmap';
import Positions from '@/components/Positions';
import TradeBar from '@/components/TradeBar';
import Chat from '@/components/Chat';

const errMsg = (e: unknown) => (e instanceof ApiError ? e.message : 'Request failed');

function snapshotsToSeries(s: Snapshot[]) {
  const out: { time: number; value: number }[] = [];
  for (const x of s) {
    const time = Math.floor(new Date(x.recorded_at).getTime() / 1000);
    const last = out[out.length - 1];
    if (last && last.time === time) last.value = x.total_value;
    else if (!last || time > last.time) out.push({ time, value: x.total_value });
  }
  return out;
}

export default function Home() {
  const { prices, history, status } = usePriceStream();
  const [tickers, setTickers] = useState<string[]>([]);
  const [portfolio, setPortfolio] = useState<Portfolio | null>(null);
  const [snapshots, setSnapshots] = useState<Snapshot[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [watchError, setWatchError] = useState('');
  const [tradeError, setTradeError] = useState('');
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [chatLoading, setChatLoading] = useState(false);
  const [chatError, setChatError] = useState('');
  const [chatOpen, setChatOpen] = useState(true);

  const loadPortfolio = useCallback(async () => {
    try {
      const [p, h] = await Promise.all([api.portfolio(), api.history()]);
      setPortfolio(p);
      setSnapshots(h.snapshots);
    } catch {}
  }, []);

  const loadWatchlist = useCallback(async () => {
    try {
      const w = await api.watchlist();
      setTickers(w.watchlist.map((x) => x.ticker));
      setSelected((s) => s ?? w.watchlist[0]?.ticker ?? null);
    } catch {}
  }, []);

  useEffect(() => {
    loadWatchlist();
    loadPortfolio();
    const t = setInterval(loadPortfolio, 15000);
    return () => clearInterval(t);
  }, [loadWatchlist, loadPortfolio]);

  const positions = useMemo(
    () =>
      (portfolio?.positions ?? []).map((p) => {
        const price = prices[p.ticker]?.price ?? p.current_price;
        const market_value = price * p.quantity;
        const unrealized_pnl = market_value - p.avg_cost * p.quantity;
        return {
          ...p,
          current_price: price,
          market_value,
          unrealized_pnl,
          unrealized_pnl_pct: p.avg_cost ? (price / p.avg_cost - 1) * 100 : 0,
        };
      }),
    [portfolio, prices],
  );
  const totalValue = portfolio ? portfolio.cash_balance + positions.reduce((s, p) => s + p.market_value, 0) : null;
  const pnlSeries = useMemo(() => snapshotsToSeries(snapshots), [snapshots]);

  const addTicker = async (t: string) => {
    setWatchError('');
    try {
      await api.addWatch(t);
      await loadWatchlist();
    } catch (e) {
      setWatchError(errMsg(e));
    }
  };
  const removeTicker = async (t: string) => {
    setWatchError('');
    try {
      await api.removeWatch(t);
      setSelected((s) => (s === t ? null : s));
      await loadWatchlist();
    } catch (e) {
      setWatchError(errMsg(e));
    }
  };
  const trade = async (ticker: string, quantity: number, side: 'buy' | 'sell') => {
    setTradeError('');
    try {
      const r = await api.trade(ticker, quantity, side);
      setPortfolio(r.portfolio);
      loadPortfolio();
    } catch (e) {
      setTradeError(errMsg(e));
    }
  };
  const send = async (text: string) => {
    setChatError('');
    setMessages((m) => [...m, { role: 'user', content: text }]);
    setChatLoading(true);
    try {
      const r = await api.chat(text);
      setMessages((m) => [...m, { role: 'assistant', content: r.message, actions: r.actions }]);
      if (r.actions?.length) {
        loadPortfolio();
        loadWatchlist();
      }
    } catch (e) {
      setChatError(
        e instanceof ApiError && e.code === 'chat_unavailable' ? 'Chat is not configured (no API key).' : errMsg(e),
      );
    } finally {
      setChatLoading(false);
    }
  };
  const reset = async () => {
    if (!window.confirm('Reset portfolio, watchlist and chat to the initial state?')) return;
    try {
      await api.reset();
      setMessages([]);
      setSelected(null);
      setWatchError('');
      setTradeError('');
      await Promise.all([loadWatchlist(), loadPortfolio()]);
    } catch {}
  };

  return (
    <div className="flex h-screen flex-col">
      <Header totalValue={totalValue} cash={portfolio?.cash_balance ?? null} status={status} onReset={reset} />
      <div className="flex min-h-0 flex-1">
        <div className="w-72 shrink-0">
          <Watchlist
            tickers={tickers}
            prices={prices}
            history={history}
            selected={selected}
            error={watchError}
            onSelect={setSelected}
            onAdd={addTicker}
            onRemove={removeTicker}
          />
        </div>
        <main className="flex min-w-0 flex-1 flex-col">
          <div className="grid min-h-0 flex-1 grid-cols-1 grid-rows-[1.2fr_1fr] gap-px bg-line">
            <div className="flex min-h-0 flex-col bg-bg p-2">
              <div className="mb-1 text-xs font-semibold uppercase text-muted">
                {selected ?? 'Select a ticker'}{' '}
                {selected && prices[selected] && (
                  <span className="font-mono text-accent">{prices[selected].price.toFixed(2)}</span>
                )}
              </div>
              <div data-testid="main-chart" className="min-h-0 flex-1">
                <LineChart data={selected ? history[selected] ?? [] : []} />
              </div>
            </div>
            <div className="grid min-h-0 grid-cols-2 gap-px bg-line">
              <div className="flex min-h-0 flex-col bg-bg p-2">
                <div className="mb-1 text-xs font-semibold uppercase text-muted">Portfolio Heatmap</div>
                <div className="min-h-0 flex-1">
                  <Heatmap positions={positions} />
                </div>
              </div>
              <div className="flex min-h-0 flex-col bg-bg p-2">
                <div className="mb-1 text-xs font-semibold uppercase text-muted">P&amp;L</div>
                <div data-testid="pnl-chart" className="min-h-0 flex-1">
                  <LineChart data={pnlSeries} color="#ecad0a" />
                </div>
              </div>
            </div>
          </div>
          <div className="max-h-40 overflow-y-auto border-t border-line bg-panel">
            <Positions positions={positions} />
          </div>
          <TradeBar selected={selected} error={tradeError} onTrade={trade} />
        </main>
        <Chat
          messages={messages}
          loading={chatLoading}
          error={chatError}
          open={chatOpen}
          onToggle={() => setChatOpen((o) => !o)}
          onSend={send}
        />
      </div>
    </div>
  );
}
