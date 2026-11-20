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


export interface AnalysisTraceEvent {
  id: number;
  timestamp: string;
  elapsed_ms: number;
  type: string;
  stage: string | null;
  title: string;
  detail: string;
  data: Record<string, any>;
}

export interface AnalysisRunDetails {
  provider: Record<string, any>;
  frame: Record<string, any>;
  market_context: Record<string, any>;
  prompts: Record<string, any>;
  validation: Array<Record<string, any>>;
  model_calls: Array<Record<string, any>>;
  strategy_files_used: string[];
  experience_loaded: Array<Record<string, any>>;
  experience_loaded_count: number;
  stage2_short_circuited: boolean;
  stage1_diagnosis: Record<string, any> | null;
  stage2_decision: Record<string, any> | null;
  usage: Record<string, number>;
  exception: Record<string, any> | null;
  chart_findings?: Record<string, any>;
  provenance?: Record<string, any>;
  historical_similarity?: Array<Record<string, any>>;
}

export interface AnalysisJobSnapshot {
  job_id: string;
  status: "queued" | "running" | "completed" | "failed";
  request: {
    market: Market;
    exchange: Exchange;
    symbol: string;
    timeframe: string;
    analysis_bars: number;
  };
  created_at: string;
  updated_at: string;
  started_at: string | null;
  finished_at: string | null;
  trace: AnalysisTraceEvent[];
  result: AnalysisResult | null;
  details: AnalysisRunDetails | null;
  error: { status_code: number; message: string } | null;
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
  trace?: AnalysisTraceEvent[];
  details?: AnalysisRunDetails;
}



export interface AnalysisHistoryItem {
  id: string;
  status: string;
  market: Market;
  exchange: Exchange;
  symbol: string;
  timeframe: string;
  model_name?: string | null;
  created_at: string;
  finished_at?: string | null;
  cycle_position?: string | null;
  direction?: string | null;
  gate_result?: string | null;
  order_type?: string | null;
  order_direction?: string | null;
  entry_price?: number | null;
  total_tokens?: number | null;
}

export interface AnalysisHistoryDetail {
  analysis_id: string;
  status: string;
  request: Record<string, any>;
  result: AnalysisResult | null;
  trace: AnalysisTraceEvent[];
  details: AnalysisRunDetails;
  provenance: Record<string, any>;
  created_at: string;
  finished_at?: string | null;
}

export interface SearchHistoryItem {
  id: number;
  query_text: string;
  market: Market;
  exchange: Exchange;
  symbol: string;
  timeframe: string;
  created_at: string;
}

export interface DatasetStats {
  sample_count: number;
  label_distribution: Record<string, number>;
  split_distribution: Record<string, number>;
  regime_distribution: Record<string, number>;
  feature_schema_version: string;
  label_schema_version: string;
  note: string;
}

export interface MarketCandleArchiveStats {
  market: Market;
  exchange: Exchange;
  symbol: string;
  timeframe: string;
  candle_count: number;
  oldest_open_time_ms: number | null;
  newest_open_time_ms: number | null;
}

export interface DerivativesObservation {
  market: "usdm";
  exchange: FuturesExchange;
  symbol: string;
  metric: string;
  period: string;
  event_time_ms: number;
  fetched_at_ms: number;
  data_kind: "historical" | "snapshot";
  value: Record<string, any>;
  raw: Record<string, any>;
  source_endpoint: string;
  quality: string;
}

export interface DerivativesCollectionReport {
  run_id: string;
  market: "usdm";
  exchange: FuturesExchange;
  symbol: string;
  period: string;
  start_time_ms: number;
  end_time_ms: number;
  fetched_at_ms: number;
  status: string;
  observation_count: number;
  stored_observations: number;
  metrics: Record<string, {
    status: string;
    records: number;
    pages?: number;
    source_endpoint?: string;
    effective_start_ms?: number;
    error?: string;
  }>;
  note: string;
}

export interface DerivativesArchiveStats {
  market: "usdm";
  exchange: FuturesExchange;
  symbol: string;
  metric: string;
  period: string;
  data_kind: "historical" | "snapshot";
  record_count: number;
  oldest_event_time_ms: number;
  newest_event_time_ms: number;
  last_fetched_at_ms: number;
}

export interface BatchBackfillResult {
  status: "completed" | "failed";
  market: Market;
  exchange: Exchange;
  symbol: string;
  timeframe: string;
  requested_bars: number;
  received_bars?: number;
  stored_candles?: number;
  archive_candles?: number;
  pages?: number;
  gaps?: number;
  truncated?: boolean;
  new_samples?: number;
  error?: string;
}

export interface BatchBackfillJob {
  job_id: string;
  status: "queued" | "running" | "completed" | "failed";
  total: number;
  completed: number;
  current: {
    market: Market;
    exchange: Exchange;
    symbol: string;
    timeframe: string;
    bars: number;
    index: number;
  } | null;
  results: BatchBackfillResult[];
  created_at: string;
  updated_at: string;
  finished_at: string | null;
}

export interface SegmentSample {
  sample_id: string;
  market: Market;
  exchange: Exchange;
  symbol: string;
  timeframe: string;
  regime: string;
  decision_time_ms: number;
  segment_start_ms: number;
  lookback_bars: number;
  horizon_bars: number;
  feature_schema_version: string;
  label_schema_version: string;
  features: Record<string, number>;
  labels: Record<string, any>;
  split: string;
  embargo_bars: number;
  similarity?: number;
}

export interface AnalysisComparison {
  left: { analysis_id: string; created_at: string; values: Record<string, any> };
  right: { analysis_id: string; created_at: string; values: Record<string, any> };
  differences: Record<string, { left: any; right: any }>;
}

export interface AIProviderPublic {
  id: string;
  name: string;
  base_url: string;
  model: string;
  thinking: boolean;
  reasoning_effort: "low" | "medium" | "high" | "max";
  context_window: number;
  has_api_key: boolean;
  api_key_masked: string;
  has_backup_glm_api_key: boolean;
  backup_glm_api_key_masked: string;
  active: boolean;
  created_at: string | null;
  updated_at: string | null;
}

export interface AIProviderList {
  providers: AIProviderPublic[];
  active_provider_id: string | null;
  saved_provider_id?: string;
  encryption: string;
}

export interface AIProviderInput {
  id?: string | null;
  name: string;
  base_url: string;
  model: string;
  api_key?: string | null;
  clear_api_key?: boolean;
  backup_glm_api_key?: string | null;
  clear_backup_glm_api_key?: boolean;
  thinking: boolean;
  reasoning_effort: "low" | "medium" | "high" | "max";
  context_window: number;
  activate: boolean;
}

export interface AIProviderTestResult {
  ok: boolean;
  model: string;
  latency_ms: number;
  response_preview?: string;
  total_tokens?: number | null;
  error?: string;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: {
      Accept: "application/json",
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
      ...(path.startsWith("/api/v1/history/") && sessionStorage.getItem("pa_ai_admin_token")
        ? { "X-PA-Admin-Token": sessionStorage.getItem("pa_ai_admin_token")! }
        : {}),
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
  startAnalysis: (
    market: Market,
    exchange: Exchange,
    symbol: string,
    timeframe: string,
    analysisBars = 100,
  ) =>
    request<AnalysisJobSnapshot>("/api/v1/analysis/jobs", {
      method: "POST",
      body: JSON.stringify({
        market,
        exchange,
        symbol,
        timeframe,
        analysis_bars: analysisBars,
      }),
    }),
  getAnalysisJob: (jobId: string) =>
    request<AnalysisJobSnapshot>(
      "/api/v1/analysis/jobs/" + encodeURIComponent(jobId),
    ),
  aiProviders: (adminToken = "") =>
    request<AIProviderList>("/api/v1/ai/providers", {
      headers: adminToken ? { "X-PA-Admin-Token": adminToken } : {},
    }),
  saveAIProvider: (payload: AIProviderInput, adminToken = "") =>
    request<AIProviderList>("/api/v1/ai/providers", {
      method: "POST",
      headers: adminToken ? { "X-PA-Admin-Token": adminToken } : {},
      body: JSON.stringify(payload),
    }),
  activateAIProvider: (id: string, adminToken = "") =>
    request<AIProviderList>("/api/v1/ai/providers/" + encodeURIComponent(id) + "/activate", {
      method: "POST",
      headers: adminToken ? { "X-PA-Admin-Token": adminToken } : {},
    }),
  deleteAIProvider: (id: string, adminToken = "") =>
    request<AIProviderList>("/api/v1/ai/providers/" + encodeURIComponent(id), {
      method: "DELETE",
      headers: adminToken ? { "X-PA-Admin-Token": adminToken } : {},
    }),
  testAIProvider: (
    payload: { id?: string | null; base_url: string; model: string; api_key?: string | null },
    adminToken = "",
  ) =>
    request<AIProviderTestResult>("/api/v1/ai/providers/test", {
      method: "POST",
      headers: adminToken ? { "X-PA-Admin-Token": adminToken } : {},
      body: JSON.stringify(payload),
    }),
  saveSearch: (payload: {
    query_text: string;
    market: Market;
    exchange: Exchange;
    symbol: string;
    timeframe: string;
  }) =>
    request<{ saved: boolean }>("/api/v1/history/searches", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  listSearches: (limit = 50) =>
    request<{ items: SearchHistoryItem[] }>("/api/v1/history/searches?limit=" + limit),
  deleteSearch: (id: number) =>
    request<{ deleted: boolean }>("/api/v1/history/searches/" + id, { method: "DELETE" }),
  listAnalysisHistory: (limit = 50, symbol?: string) =>
    request<{ items: AnalysisHistoryItem[] }>(
      "/api/v1/history/analyses?limit=" + limit + (symbol ? "&symbol=" + encodeURIComponent(symbol) : ""),
    ),
  getAnalysisHistory: (id: string) =>
    request<AnalysisHistoryDetail>("/api/v1/history/analyses/" + encodeURIComponent(id)),
  compareAnalysisHistory: (leftId: string, rightId: string) =>
    request<AnalysisComparison>(
      "/api/v1/history/analyses/compare?left_id=" + encodeURIComponent(leftId) +
      "&right_id=" + encodeURIComponent(rightId),
    ),
  deleteAnalysisHistory: (id: string) =>
    request<{ deleted: boolean }>("/api/v1/history/analyses/" + encodeURIComponent(id), { method: "DELETE" }),
  buildHistoricalDataset: (payload: {
    market: Market; exchange: Exchange; symbol: string; timeframe: string; limit: number;
  }) =>
    request<{ requested_bars: number; received_bars: number; closed_bars: number; stored_candles: number; archive_candles: number; new_samples: number; dataset: DatasetStats; note: string }>(
      "/api/v1/history/dataset/build",
      { method: "POST", body: JSON.stringify(payload) },
    ),
  backfillDataset: (payload: {
    market: Market; exchange: Exchange; symbol: string; timeframe: string; bars: number;
  }) =>
    request<{
      requested_bars: number; received_bars: number; stored_candles: number; archive_candles: number;
      pages: number; gaps: number; truncated: boolean; new_samples: number;
      dataset: DatasetStats; note: string;
    }>("/api/v1/history/dataset/backfill", {
      method: "POST", body: JSON.stringify(payload),
    }),
  startBatchBackfill: (jobs: Array<{
    market: Market; exchange: Exchange; symbol: string; timeframe: string; bars: number;
  }>) =>
    request<BatchBackfillJob>("/api/v1/history/dataset/batch-backfill", {
      method: "POST",
      body: JSON.stringify({ jobs }),
    }),
  getBatchBackfill: (jobId: string) =>
    request<BatchBackfillJob>(
      "/api/v1/history/dataset/batch-backfill/" + encodeURIComponent(jobId),
    ),
  marketCandleStats: (payload: {
    market?: Market; exchange?: Exchange; symbol?: string; timeframe?: string;
  } = {}) => {
    const query = new URLSearchParams(
      Object.entries(payload).filter((entry): entry is [string, string] => Boolean(entry[1])),
    );
    return request<{ items: MarketCandleArchiveStats[] }>(
      "/api/v1/history/candles/stats?" + query.toString(),
    );
  },
  backfillDerivatives: (payload: {
    market: "usdm"; exchange: FuturesExchange; symbol: string;
    period: string; start_ms: number; end_ms: number;
  }) =>
    request<DerivativesCollectionReport>("/api/v1/history/derivatives/backfill", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  listDerivatives: (payload: {
    exchange: FuturesExchange; symbol: string; market?: "usdm";
    start_ms?: number; end_ms?: number; metric?: string; period?: string;
    data_kind?: "historical" | "snapshot"; limit?: number;
  }) => {
    const query = new URLSearchParams(
      Object.entries(payload).filter((entry): entry is [string, string] =>
        entry[1] !== undefined && entry[1] !== null && entry[1] !== "",
      ).map(([key, value]) => [key, String(value)]),
    );
    return request<{ items: DerivativesObservation[] }>(
      "/api/v1/history/derivatives?" + query.toString(),
    );
  },
  derivativesStats: (payload: {
    exchange?: FuturesExchange; symbol?: string; market?: "usdm";
  } = {}) => {
    const query = new URLSearchParams(
      Object.entries(payload).filter((entry): entry is [string, string] => Boolean(entry[1])),
    );
    return request<{ items: DerivativesArchiveStats[] }>(
      "/api/v1/history/derivatives/stats?" + query.toString(),
    );
  },
  derivativesRuns: (payload: {
    exchange: FuturesExchange; symbol: string; market?: "usdm"; limit?: number;
  }) => {
    const query = new URLSearchParams(
      Object.entries(payload).filter((entry): entry is [string, string] =>
        entry[1] !== undefined && entry[1] !== null,
      ).map(([key, value]) => [key, String(value)]),
    );
    return request<{ items: Array<Record<string, any>> }>(
      "/api/v1/history/derivatives/runs?" + query.toString(),
    );
  },
  evaluateDataset: (payload: { market: Market; exchange: Exchange; symbol: string; timeframe: string }) => {
    const query = new URLSearchParams(payload);
    return request<{
      status: "evaluated" | "insufficient_data";
      train_count: number; test_count: number;
      rule_name?: string; majority_class_from_train?: string;
      rule_baseline?: { count: number; accuracy: number; balanced_accuracy: number; macro_f1: number };
      train_majority_baseline?: { count: number; accuracy: number; balanced_accuracy: number; macro_f1: number };
      note: string;
    }>("/api/v1/history/dataset/evaluation?" + query.toString());
  },
  datasetStats: (payload: { market?: Market; exchange?: Exchange; symbol?: string; timeframe?: string } = {}) => {
    const query = new URLSearchParams(
      Object.entries(payload).filter((entry): entry is [string, string] => Boolean(entry[1])),
    );
    return request<DatasetStats>("/api/v1/history/dataset/stats?" + query.toString());
  },
  listDatasetSamples: (payload: {
    market?: Market; exchange?: Exchange; symbol?: string; timeframe?: string; split?: string; limit?: number;
  } = {}) => {
    const query = new URLSearchParams(
      Object.entries(payload).filter((entry): entry is [string, string] => entry[1] !== undefined && entry[1] !== null)
        .map(([key, value]) => [key, String(value)]),
    );
    return request<{ items: SegmentSample[] }>("/api/v1/history/dataset/samples?" + query.toString());
  },
  similarSegments: (payload: {
    market: Market; exchange: Exchange; symbol: string; timeframe: string; limit?: number;
  }) => {
    const query = new URLSearchParams({
      market: payload.market,
      exchange: payload.exchange,
      symbol: payload.symbol,
      timeframe: payload.timeframe,
      limit: String(payload.limit ?? 5),
    });
    return request<{ current: Record<string, any> | null; items: SegmentSample[]; note: string }>(
      "/api/v1/history/dataset/similar?" + query.toString(),
    );
  },
};
