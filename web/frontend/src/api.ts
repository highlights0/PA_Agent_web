export type Market = "spot" | "usdm";

export interface Kline {
  open_time: number;
  close_time: number;
  open: string;
  high: string;
  low: string;
  close: string;
  volume: string;
  quote_volume: string;
  trades: number;
  taker_buy_base_volume: string;
  taker_buy_quote_volume: string;
  closed: boolean;
}

export interface Ticker {
  symbol: string;
  last_price: string;
  price_change: string;
  price_change_percent: string;
  high_price: string;
  low_price: string;
  volume: string;
  quote_volume: string;
}

async function request<T>(path: string): Promise<T> {
  const response = await fetch(path, { headers: { Accept: "application/json" } });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || "请求失败 (" + response.status + ")");
  }
  return response.json() as Promise<T>;
}

export const api = {
  klines: (market: Market, symbol: string, interval: string, limit = 200) =>
    request<Kline[]>(
      "/api/v1/market/" + market + "/" + encodeURIComponent(symbol) +
      "/klines?interval=" + encodeURIComponent(interval) + "&limit=" + limit,
    ),
  ticker: (market: Market, symbol: string) =>
    request<Ticker>("/api/v1/market/" + market + "/" + encodeURIComponent(symbol) + "/ticker"),
};
