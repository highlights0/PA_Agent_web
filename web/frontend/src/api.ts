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

export interface AnalysisResult {
  analysis_id: string;
  status: "completed" | "failed";
  market: Market;
  symbol: string;
  timeframe: string;
  bar_count: number;
  events: string[];
  stage1_diagnosis: Record<string, any> | null;
  stage2_decision: Record<string, any> | null;
  usage: Record<string, number>;
  error: Record<string, any> | null;
  quota_remaining: number;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: {
      Accept: "application/json",
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
      ...init?.headers,
    },
  });
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
  analyze: (market: Market, symbol: string, timeframe: string, analysisBars = 100) =>
    request<AnalysisResult>("/api/v1/analysis", {
      method: "POST",
      body: JSON.stringify({
        market,
        symbol,
        timeframe,
        analysis_bars: analysisBars,
      }),
    }),
};
