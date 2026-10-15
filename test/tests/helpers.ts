import { expect, Page, APIRequestContext } from '@playwright/test';

export const DEFAULTS = ['AAPL','GOOGL','MSFT','AMZN','TSLA','NVDA','META','JPM','V','NFLX'];
export const money = (s: string | null) => Number((s ?? '').replace(/[^0-9.\-]/g, ''));

export async function resetApi(request: APIRequestContext) {
  const r = await request.post('/api/reset');
  expect(r.ok()).toBeTruthy();
}

export async function open(page: Page) {
  await page.goto('/');
  await expect(page.getByTestId('watchlist-row-AAPL')).toBeVisible();
}

export async function cash(page: Page) {
  return money(await page.getByTestId('header-cash').innerText());
}

export async function trade(page: Page, ticker: string, qty: number, side: 'buy' | 'sell') {
  await page.getByTestId('trade-ticker').fill(ticker);
  await page.getByTestId('trade-quantity').fill(String(qty));
  await page.getByTestId(`trade-${side}`).click();
}
