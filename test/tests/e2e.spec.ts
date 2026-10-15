import { test, expect } from '@playwright/test';
import { DEFAULTS, resetApi, open, cash, trade, money } from './helpers';

test.beforeEach(async ({ request }) => {
  await resetApi(request);
});

test('fresh start: 10 tickers, $10k, prices streaming', async ({ page }) => {
  await open(page);
  for (const t of DEFAULTS) await expect(page.getByTestId(`watchlist-row-${t}`)).toBeVisible();
  await expect(page.getByTestId('header-cash')).toContainText('10,000');
  await expect(page.getByTestId('connection-status')).toHaveAttribute('data-status', 'connected');
  const el = page.getByTestId('watchlist-price-AAPL');
  const first = await el.innerText();
  await expect.poll(async () => el.innerText(), { timeout: 30_000 }).not.toBe(first);
});

test('remove and re-add ticker; unknown ticker errors', async ({ page }) => {
  await open(page);
  await page.getByTestId('watchlist-remove-V').click();
  await expect(page.getByTestId('watchlist-row-V')).toHaveCount(0);
  await page.getByTestId('watchlist-add-input').fill('v');
  await page.getByTestId('watchlist-add-button').click();
  await expect(page.getByTestId('watchlist-row-V')).toBeVisible();
  await page.getByTestId('watchlist-add-input').fill('ZZZZ');
  await page.getByTestId('watchlist-add-button').click();
  await expect(page.getByTestId('watchlist-error')).toBeVisible();
  await expect(page.getByTestId('watchlist-row-ZZZZ')).toHaveCount(0);
});

test('buy then sell shares', async ({ page }) => {
  await open(page);
  await trade(page, 'AAPL', 5, 'buy');
  await expect(page.getByTestId('position-row-AAPL')).toBeVisible();
  await expect.poll(() => cash(page)).toBeLessThan(10000);
  const afterBuy = await cash(page);
  await trade(page, 'AAPL', 2, 'sell');
  await expect.poll(() => cash(page)).toBeGreaterThan(afterBuy);
  await expect(page.getByTestId('position-row-AAPL')).toBeVisible();
  await trade(page, 'AAPL', 3, 'sell');
  await expect(page.getByTestId('position-row-AAPL')).toHaveCount(0);
});

test('invalid trades show errors', async ({ page }) => {
  await open(page);
  await trade(page, 'AAPL', 1_000_000, 'buy');
  await expect(page.getByTestId('trade-error')).toBeVisible();
  await expect(page.getByTestId('position-row-AAPL')).toHaveCount(0);
});

test('heatmap and P&L chart render with data', async ({ page, request }) => {
  await open(page);
  await trade(page, 'MSFT', 3, 'buy');
  await expect(page.getByTestId('position-row-MSFT')).toBeVisible();
  await expect(page.getByTestId('heatmap')).toBeVisible();
  await expect(page.getByTestId('heatmap').locator('svg rect, svg path').first()).toBeVisible();
  await expect(page.getByTestId('pnl-chart').locator('canvas').first()).toBeVisible();
  const h = await (await request.get('/api/portfolio/history')).json();
  expect(h.snapshots.length).toBeGreaterThanOrEqual(2);
});

test('AI chat (mock): buy shows inline trade', async ({ page }) => {
  await open(page);
  if (!(await page.getByTestId('chat-input').isVisible())) {
    await page.getByTestId('chat-toggle').click();
  }
  await page.getByTestId('chat-input').fill('please buy some');
  await page.getByTestId('chat-send').click();
  await expect(page.getByTestId('chat-action').first()).toBeVisible();
  await expect(page.getByTestId('position-row-AAPL')).toBeVisible();
});

test('reset restores initial state', async ({ page }) => {
  await open(page);
  await trade(page, 'AAPL', 1, 'buy');
  await expect(page.getByTestId('position-row-AAPL')).toBeVisible();
  await page.getByTestId('watchlist-remove-V').click();
  await expect(page.getByTestId('watchlist-row-V')).toHaveCount(0);
  page.once('dialog', d => d.accept());
  await page.getByTestId('reset-button').click();
  await expect(page.getByTestId('position-row-AAPL')).toHaveCount(0);
  await expect(page.getByTestId('watchlist-row-V')).toBeVisible();
  await expect(page.getByTestId('header-cash')).toContainText('10,000');
});

test('SSE reconnects after disconnect', async ({ page, context }) => {
  await open(page);
  await expect(page.getByTestId('connection-status')).toHaveAttribute('data-status', 'connected');
  await context.setOffline(true);
  await expect(page.getByTestId('connection-status')).not.toHaveAttribute('data-status', 'connected', { timeout: 30_000 });
  await context.setOffline(false);
  await expect(page.getByTestId('connection-status')).toHaveAttribute('data-status', 'connected', { timeout: 40_000 });
});
