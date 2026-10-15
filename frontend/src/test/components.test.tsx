import { render, screen, fireEvent } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import Watchlist, { PriceCell } from '@/components/Watchlist';
import Positions from '@/components/Positions';
import Chat from '@/components/Chat';
import Header from '@/components/Header';
import TradeBar from '@/components/TradeBar';
import { pct, usd } from '@/lib/format';

vi.mock('@/components/LineChart', () => ({ default: () => <div /> }));

const price = (ticker: string, p: number, baseline = 100) => ({
  ticker, price: p, previous_price: p, baseline_price: baseline, timestamp: '', direction: 'flat' as const,
});

describe('PriceCell', () => {
  it('flashes up then down on price change', () => {
    const { rerender } = render(<PriceCell ticker="AAPL" price={100} />);
    const cell = screen.getByTestId('watchlist-price-AAPL');
    expect(cell).toHaveAttribute('data-direction', 'flat');
    rerender(<PriceCell ticker="AAPL" price={101} />);
    expect(cell).toHaveAttribute('data-direction', 'up');
    expect(cell.className).toContain('flash-up');
    rerender(<PriceCell ticker="AAPL" price={99} />);
    expect(cell).toHaveAttribute('data-direction', 'down');
    expect(cell.className).toContain('flash-down');
  });
});

describe('Watchlist', () => {
  const setup = () => {
    const h = { onSelect: vi.fn(), onAdd: vi.fn(), onRemove: vi.fn() };
    render(
      <Watchlist tickers={['AAPL', 'MSFT']} prices={{ AAPL: price('AAPL', 105) }} history={{}}
        selected="AAPL" error="Unknown ticker" {...h} />,
    );
    return h;
  };
  it('renders rows, change %, and error', () => {
    setup();
    expect(screen.getByTestId('watchlist-row-MSFT')).toBeInTheDocument();
    expect(screen.getByTestId('watchlist-change-AAPL')).toHaveTextContent('+5.00%');
    expect(screen.getByTestId('watchlist-error')).toHaveTextContent('Unknown ticker');
  });
  it('adds, removes and selects', () => {
    const h = setup();
    fireEvent.change(screen.getByTestId('watchlist-add-input'), { target: { value: 'nflx' } });
    fireEvent.click(screen.getByTestId('watchlist-add-button'));
    expect(h.onAdd).toHaveBeenCalledWith('NFLX');
    fireEvent.click(screen.getByTestId('watchlist-remove-MSFT'));
    expect(h.onRemove).toHaveBeenCalledWith('MSFT');
    expect(h.onSelect).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId('watchlist-row-MSFT'));
    expect(h.onSelect).toHaveBeenCalledWith('MSFT');
  });
});

describe('Portfolio display', () => {
  it('shows position rows and formats money', () => {
    render(
      <Positions positions={[{ ticker: 'AAPL', quantity: 10, avg_cost: 190, current_price: 195,
        market_value: 1950, unrealized_pnl: 50, unrealized_pnl_pct: 2.63 }]} />,
    );
    expect(screen.getByTestId('position-row-AAPL')).toHaveTextContent('$1,950.00');
    expect(screen.getByTestId('position-row-AAPL')).toHaveTextContent('+2.63%');
    expect(usd(8100)).toBe('$8,100.00');
    expect(pct(-1.234)).toBe('-1.23%');
  });
  it('header shows totals and status', () => {
    render(<Header totalValue={10050} cash={8100} status="reconnecting" onReset={() => {}} />);
    expect(screen.getByTestId('header-total-value')).toHaveTextContent('$10,050.00');
    expect(screen.getByTestId('header-cash')).toHaveTextContent('$8,100.00');
    expect(screen.getByTestId('connection-status')).toHaveAttribute('data-status', 'reconnecting');
  });
});

describe('Chat', () => {
  it('renders messages, actions, failures and loading', () => {
    const onSend = vi.fn();
    render(
      <Chat open loading error="" onToggle={() => {}} onSend={onSend}
        messages={[
          { role: 'user', content: 'buy' },
          { role: 'assistant', content: 'Done', actions: [
            { type: 'buy', ticker: 'AAPL', quantity: 1, price: 190.12, status: 'executed' },
            { type: 'buy', ticker: 'TSLA', quantity: 500, status: 'failed', error: { code: 'insufficient_cash', message: 'Need more cash' } },
          ] },
        ]} />,
    );
    expect(screen.getAllByTestId('chat-message')).toHaveLength(2);
    const actions = screen.getAllByTestId('chat-action');
    expect(actions[0]).toHaveTextContent('Bought 1 AAPL @ $190.12');
    expect(actions[1]).toHaveAttribute('data-status', 'failed');
    expect(actions[1]).toHaveTextContent('Need more cash');
    expect(screen.getByTestId('chat-loading')).toBeInTheDocument();
    fireEvent.change(screen.getByTestId('chat-input'), { target: { value: 'hi' } });
    fireEvent.submit(screen.getByTestId('chat-input').closest('form')!);
    expect(onSend).not.toHaveBeenCalled();
  });
});

describe('TradeBar', () => {
  it('submits trades', () => {
    const onTrade = vi.fn();
    render(<TradeBar selected={null} error="" onTrade={onTrade} />);
    fireEvent.change(screen.getByTestId('trade-ticker'), { target: { value: 'aapl' } });
    fireEvent.change(screen.getByTestId('trade-quantity'), { target: { value: '2.5' } });
    fireEvent.click(screen.getByTestId('trade-sell'));
    expect(onTrade).toHaveBeenCalledWith('AAPL', 2.5, 'sell');
  });
});
