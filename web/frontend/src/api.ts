export type Market = "spot" | "usdm";
export type Exchange = "binance" | "binance_us" | "coinbase" | "gate";
export type FuturesExchange = "binance" | "gate";

export interface Kline {
  open_time: number;
  close_time: number;
  open: string;
  high: string;
  low: string;
  close: string;
  volume: string;
  quote_volume: string;
  trades: number | null;
  taker_buy_base_volume: string;
  taker_buy_quote_volume: string;
  closed: boolean;
  exchange: Exchange;
  market_type: Market;
  volume_unit: string;
  contract_volume?: string | null;
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
  exchange: Exchange;
}

export interface FuturesContext {
  symbol: string;
  exchange: FuturesExchange;
  contract?: string | null;
  mark_price?: string | null;
  index_price?: string | null;
  funding_rate?: string | null;
  next_funding_time?: number | null;
  funding_interval_seconds?: number | null;
  open_interest?: string | null;
  open_interest_usd?: string | null;
  open_interest_unit?: string | null;
  open_interest_change_pct?: number | null;
  global_long_short_ratio?: string | null;
  taker_buy_sell_ratio?: string | null;
  taker_long_short_ratio?: string | null;
  top_trader_long_short_account_ratio?: string | null;
  top_trader_long_short_position_ratio?: string | null;
  long_liquidation_usd_3h?: string | null;
  short_liquidation_usd_3h?: string | null;
  long_liquidation_contracts_3h?: string | null;
  short_liquidation_contracts_3h?: string | null;
  stats_interval?: string | null;
  stats_window_minutes?: number | null;
  stats_history: Array<Record<string, any>>;
  funding_rate_history: Array<Record<string, any>>;
  data_timestamp: number;
  data_quality: Record<string, string>;
}

export interface AnalysisResult {
  analysis_id: string;
  status: "completed" | "failed";
  market: Market;
  exchange: Exchange;
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
  klines: (market: Market, exchange: Exchange, symbol: string, interval: string, limit = 200) =>
    request<Kline[]>(
      "/api/v1/market/" + market + "/" + encodeURIComponent(symbol) +
      "/klines?exchange=" + exchange + "&interval=" + encodeURIComponent(interval) + "&limit=" + limit,
    ),
  ticker: (market: Market, exchange: Exchange, symbol: string) =>
    request<Ticker>(
      "/api/v1/market/" + market + "/" + encodeURIComponent(symbol) + "/ticker?exchange=" + exchange,
    ),
  futuresContext: (exchange: FuturesExchange, symbol: string, period = "5m") =>
    request<FuturesContext>(
      "/api/v1/market/usdm/" + encodeURIComponent(symbol) +
      "/context?exchange=" + exchange + "&period=" + encodeURIComponent(period),
    ),
  analyze: (market: Market, exchange: Exchange, symbol: string, timeframe: string, analysisBars = 100) =>
    request<AnalysisResult>("/api/v1/analysis", {
      method: "POST",
      body: JSON.stringify({
        market,
        exchange,
        symbol,
        timeframe,
        analysis_bars: analysisBars,
      }),
    }),
};
