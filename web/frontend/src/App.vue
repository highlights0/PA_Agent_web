<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { ColorType, createChart, type IChartApi, type ISeriesApi } from "lightweight-charts";
import {
  api,
  type AnalysisResult,
  type Exchange,
  type FuturesContext,
  type FuturesExchange,
  type Kline,
  type Market,
  type Ticker,
} from "./api";

const market = ref<Market>("spot");
const exchange = ref<Exchange>("coinbase");
const symbol = ref("BTC-USD");
const interval = ref("15m");
const klines = ref<Kline[]>([]);
const ticker = ref<Ticker | null>(null);
const futuresContext = ref<FuturesContext | null>(null);
const analysis = ref<AnalysisResult | null>(null);
const loading = ref(false);
const contextLoading = ref(false);
const analysisLoading = ref(false);
const error = ref("");
const contextError = ref("");
const analysisError = ref("");
const chartHost = ref<HTMLDivElement | null>(null);
let chart: IChartApi | null = null;
let candleSeries: ISeriesApi<"Candlestick"> | null = null;
let resizeObserver: ResizeObserver | null = null;
let refreshTimer: number | undefined;
let contextTimer: number | undefined;

const changePositive = computed(() => Number(ticker.value?.price_change_percent ?? 0) >= 0);
const diagnosis = computed<Record<string, any>>(() => analysis.value?.stage1_diagnosis ?? {});
const decision = computed<Record<string, any>>(() => analysis.value?.stage2_decision?.decision ?? {});
const exchangeLabel = computed(() => ({
  binance: market.value === "usdm" ? "Binance Global USD-M" : "Binance Global Spot",
  binance_us: "Binance.US Spot",
  coinbase: "Coinbase Exchange Spot",
  gate: "Gate.io USDT Perpetual",
}[exchange.value]));

function createMarketChart() {
  if (!chartHost.value || chart) return;
  chart = createChart(chartHost.value, {
    width: chartHost.value.clientWidth,
    height: 430,
    layout: { background: { type: ColorType.Solid, color: "#0b1220" }, textColor: "#94a3b8" },
    grid: { vertLines: { color: "#172235" }, horzLines: { color: "#172235" } },
    rightPriceScale: { borderColor: "#263449" },
    timeScale: { borderColor: "#263449", timeVisible: true, secondsVisible: false },
    crosshair: { vertLine: { color: "#64748b", labelBackgroundColor: "#334155" }, horzLine: { color: "#64748b", labelBackgroundColor: "#334155" } },
  });
  candleSeries = chart.addCandlestickSeries({
    upColor: "#16a34a",
    downColor: "#ef4444",
    borderUpColor: "#16a34a",
    borderDownColor: "#ef4444",
    wickUpColor: "#16a34a",
    wickDownColor: "#ef4444",
  });
  resizeObserver = new ResizeObserver((entries) => {
    const width = entries[0]?.contentRect.width;
    if (width && chart) chart.applyOptions({ width });
  });
  resizeObserver.observe(chartHost.value);
}

async function loadMarket() {
  loading.value = true;
  error.value = "";
  try {
    const normalized = symbol.value.trim().toUpperCase();
    if (!/^[A-Z0-9_-]{3,32}$/.test(normalized)) throw new Error("交易对格式不正确");
    symbol.value = normalized;
    const [bars, quote] = await Promise.all([
      api.klines(market.value, exchange.value, normalized, interval.value, 200),
      api.ticker(market.value, exchange.value, normalized),
    ]);
    klines.value = bars;
    ticker.value = quote;
    await nextTick();
    createMarketChart();
    if (candleSeries) {
      candleSeries.setData(
        bars.map((bar) => ({
          time: Math.floor(bar.open_time / 1000) as any,
          open: Number(bar.open),
          high: Number(bar.high),
          low: Number(bar.low),
          close: Number(bar.close),
        })),
      );
      chart?.timeScale().fitContent();
    }
  } catch (err) {
    error.value = err instanceof Error ? err.message : "行情加载失败";
  } finally {
    loading.value = false;
  }
}

async function loadFuturesContext() {
  if (market.value !== "usdm" || !["gate", "binance"].includes(exchange.value)) {
    futuresContext.value = null;
    contextError.value = "";
    return;
  }
  contextLoading.value = true;
  contextError.value = "";
  try {
    futuresContext.value = await api.futuresContext(
      exchange.value as FuturesExchange,
      symbol.value.trim().toUpperCase(),
      "5m",
    );
  } catch (err) {
    futuresContext.value = null;
    contextError.value = err instanceof Error ? err.message : "合约统计数据加载失败";
  } finally {
    contextLoading.value = false;
  }
}

async function runAnalysis() {
  analysisLoading.value = true;
  analysisError.value = "";
  analysis.value = null;
  try {
    const normalized = symbol.value.trim().toUpperCase();
    const result = await api.analyze(market.value, exchange.value, normalized, interval.value, 100);
    analysis.value = result;
    if (result.status === "failed") {
      analysisError.value = result.error?.message || "AI 分析未通过校验";
    }
  } catch (err) {
    analysisError.value = err instanceof Error ? err.message : "AI 分析失败";
  } finally {
    analysisLoading.value = false;
  }
}

function queryMarket() {
  void loadMarket();
  void loadFuturesContext();
}

function onMarketChange() {
  analysis.value = null;
  futuresContext.value = null;
  if (market.value === "usdm") {
    exchange.value = "gate";
    if (symbol.value.includes("-")) symbol.value = "BTCUSDT";
    if (symbol.value.includes("_")) symbol.value = symbol.value.replace("_", "");
  } else {
    if (exchange.value === "gate" || exchange.value === "binance") {
      exchange.value = "coinbase";
      symbol.value = "BTC-USD";
    } else if (!symbol.value.includes("-")) {
      symbol.value = "BTCUSDT";
    }
  }
  queryMarket();
}

function onExchangeChange() {
  analysis.value = null;
  futuresContext.value = null;
  if (market.value === "usdm") {
    if (symbol.value.includes("-")) symbol.value = "BTCUSDT";
    if (symbol.value.includes("_")) symbol.value = symbol.value.replace("_", "");
  } else if (exchange.value === "coinbase") {
    if (!symbol.value.includes("-")) symbol.value = "BTC-USD";
  } else if (symbol.value.includes("-")) {
    symbol.value = "BTCUSDT";
  }
  queryMarket();
}

function formatNumber(value: string | undefined | null) {
  if (value === undefined || value === null || value === "") return "--";
  const number = Number(value);
  return Number.isFinite(number)
    ? number.toLocaleString("en-US", { maximumFractionDigits: 6 })
    : "--";
}

function formatFundingRate(value: string | undefined | null) {
  if (value === undefined || value === null || value === "") return "--";
  const number = Number(value);
  return Number.isFinite(number) ? (number * 100).toFixed(4) + "%" : "--";
}

function formatPercent(value: number | undefined | null) {
  if (value === undefined || value === null || !Number.isFinite(value)) return "--";
  return (value >= 0 ? "+" : "") + value.toFixed(2) + "%";
}

function formatTimestamp(value: number | undefined | null) {
  if (!value) return "--";
  return new Date(value).toLocaleString();
}

watch(interval, () => {
  analysis.value = null;
  void loadMarket();
});
onMounted(() => {
  void loadMarket();
  void loadFuturesContext();
  refreshTimer = window.setInterval(() => void loadMarket(), 15_000);
  contextTimer = window.setInterval(() => void loadFuturesContext(), 60_000);
});
onBeforeUnmount(() => {
  if (refreshTimer) window.clearInterval(refreshTimer);
  if (contextTimer) window.clearInterval(contextTimer);
  resizeObserver?.disconnect();
  chart?.remove();
  chart = null;
  candleSeries = null;
});
</script>

<template>
  <main class="app-shell">
    <header class="topbar">
      <div class="brand">
        <div class="brand-mark">PA</div>
        <div>
          <div class="brand-title">PA Crypto Agent</div>
          <div class="brand-subtitle">PRICE ACTION MARKET WORKSPACE</div>
        </div>
      </div>
      <div class="connection"><span class="live-dot"></span> 多交易所公开行情</div>
    </header>

    <section class="page-heading">
      <div>
        <div class="eyebrow">MARKET OVERVIEW</div>
        <h1>加密货币行情中心</h1>
        <p>多交易所现货行情 · Gate USDT 永续合约统计 · 原版 Price Action 两阶段分析</p>
      </div>
      <div class="refresh-state">{{ loading ? "正在同步…" : "K 线每 15 秒刷新" }}</div>
    </section>

    <section class="toolbar panel">
      <label>
        <span>市场</span>
        <select v-model="market" @change="onMarketChange">
          <option value="spot">现货 Spot</option>
          <option value="usdm">USDT 永续合约</option>
        </select>
      </label>
      <label v-if="market === 'spot'">
        <span>交易所</span>
        <select v-model="exchange" @change="onExchangeChange">
          <option value="coinbase">Coinbase Exchange</option>
          <option value="binance_us">Binance.US</option>
          <option value="binance">Binance Global</option>
        </select>
      </label>
      <label v-else>
        <span>合约数据源</span>
        <select v-model="exchange" @change="onExchangeChange">
          <option value="gate">Gate.io USDT 永续</option>
          <option value="binance">Binance Global USDⓈ-M</option>
        </select>
      </label>
      <label class="symbol-field">
        <span>交易对</span>
        <input v-model="symbol" spellcheck="false" @keyup.enter="queryMarket" />
      </label>
      <label>
        <span>周期</span>
        <select v-model="interval">
          <option v-for="tf in ['1m','5m','15m','30m','1h','4h','1d']" :key="tf" :value="tf">{{ tf }}</option>
        </select>
      </label>
      <button class="secondary-button" :disabled="loading" @click="queryMarket">查询行情</button>
      <button class="primary-button" :disabled="analysisLoading" @click="runAnalysis">
        {{ analysisLoading ? "AI 分析中…" : "AI 两阶段分析" }}
      </button>
    </section>

    <div v-if="market === 'usdm' && exchange === 'binance'" class="notice-banner">
      Binance Global USD-M 在当前网络已返回 HTTP 451。请切换 Gate.io USDT 永续；不会使用现货数据替代合约数据。
    </div>
    <div v-if="error" class="error-banner">{{ error }}</div>
    <div v-if="contextError" class="error-banner">{{ contextError }}</div>
    <div v-if="analysisError" class="error-banner">{{ analysisError }}</div>

    <section class="stats-grid">
      <article class="stat-card panel">
        <span class="stat-label">最新价格</span>
        <strong class="price-value">{{ formatNumber(ticker?.last_price) }}</strong>
        <span class="stat-foot">{{ symbol }} · {{ exchangeLabel }}</span>
      </article>
      <article class="stat-card panel">
        <span class="stat-label">24 小时涨跌</span>
        <strong :class="changePositive ? 'positive' : 'negative'">
          {{ ticker ? (changePositive ? "+" : "") + ticker.price_change_percent + "%" : "--" }}
        </strong>
        <span class="stat-foot">24H Price Change</span>
      </article>
      <article class="stat-card panel">
        <span class="stat-label">24 小时最高</span>
        <strong>{{ formatNumber(ticker?.high_price) }}</strong>
        <span class="stat-foot">24H High</span>
      </article>
      <article class="stat-card panel">
        <span class="stat-label">24 小时最低</span>
        <strong>{{ formatNumber(ticker?.low_price) }}</strong>
        <span class="stat-foot">24H Low</span>
      </article>
    </section>

    <section v-if="market === 'usdm' && exchange === 'gate'" class="futures-context panel">
      <div class="panel-heading">
        <div>
          <h2>Gate 合约市场统计</h2>
          <p>公开接口 · 5 分钟统计 · 每 60 秒刷新</p>
        </div>
        <span class="data-badge">{{ contextLoading ? "SYNCING" : "GATE PUBLIC DATA" }}</span>
      </div>
      <div v-if="futuresContext" class="futures-metrics">
        <div class="futures-metric">
          <span>资金费率</span>
          <strong>{{ formatFundingRate(futuresContext.funding_rate) }}</strong>
          <small>结算周期 {{ futuresContext.funding_interval_seconds ? (futuresContext.funding_interval_seconds / 3600) + " 小时" : "--" }}</small>
        </div>
        <div class="futures-metric">
          <span>持仓量 USD</span>
          <strong>{{ formatNumber(futuresContext.open_interest_usd) }}</strong>
          <small>合约张数 {{ formatNumber(futuresContext.open_interest) }}</small>
        </div>
        <div class="futures-metric">
          <span>OI 统计窗口变化</span>
          <strong>{{ formatPercent(futuresContext.open_interest_change_pct) }}</strong>
          <small>窗口 {{ futuresContext.stats_window_minutes ?? "--" }} 分钟</small>
        </div>
        <div class="futures-metric">
          <span>全体账户多空比</span>
          <strong>{{ formatNumber(futuresContext.global_long_short_ratio) }}</strong>
          <small>Gate lsr_account</small>
        </div>
        <div class="futures-metric">
          <span>主动交易多空比</span>
          <strong>{{ formatNumber(futuresContext.taker_long_short_ratio) }}</strong>
          <small>Gate lsr_taker</small>
        </div>
        <div class="futures-metric">
          <span>大户账户多空比</span>
          <strong>{{ formatNumber(futuresContext.top_trader_long_short_account_ratio) }}</strong>
          <small>Top trader accounts</small>
        </div>
        <div class="futures-metric">
          <span>大户持仓多空比</span>
          <strong>{{ formatNumber(futuresContext.top_trader_long_short_position_ratio) }}</strong>
          <small>Top trader positions</small>
        </div>
        <div class="futures-metric liquidation-long">
          <span>近 {{ futuresContext.stats_window_minutes ?? "--" }} 分钟多头爆仓</span>
          <strong>{{ formatNumber(futuresContext.long_liquidation_usd_3h) }} USDT</strong>
          <small>合约张数 {{ formatNumber(futuresContext.long_liquidation_contracts_3h) }}</small>
        </div>
        <div class="futures-metric liquidation-short">
          <span>近 {{ futuresContext.stats_window_minutes ?? "--" }} 分钟空头爆仓</span>
          <strong>{{ formatNumber(futuresContext.short_liquidation_usd_3h) }} USDT</strong>
          <small>合约张数 {{ formatNumber(futuresContext.short_liquidation_contracts_3h) }}</small>
        </div>
      </div>
      <div v-else class="context-empty">
        {{ contextLoading ? "正在获取合约统计…" : "暂时没有可用的合约统计数据" }}
      </div>
      <div v-if="futuresContext" class="context-meta">
        <span>标记价格：{{ formatNumber(futuresContext.mark_price) }}</span>
        <span>指数价格：{{ formatNumber(futuresContext.index_price) }}</span>
        <span>下一次资金费结算：{{ formatTimestamp(futuresContext.next_funding_time) }}</span>
        <span>数据时间：{{ formatTimestamp(futuresContext.data_timestamp) }}</span>
      </div>
      <div v-if="futuresContext" class="context-quality">
        <span v-for="(value, key) in futuresContext.data_quality" :key="key">{{ key }}: {{ value }}</span>
      </div>
    </section>

    <section v-if="analysis" class="analysis-results panel">
      <div class="panel-heading">
        <div>
          <h2>AI Price Action 分析</h2>
          <p>{{ analysis.symbol }} · {{ analysis.exchange }} · {{ analysis.market }} · {{ analysis.timeframe }} · {{ analysis.bar_count }} 根已收盘 K 线</p>
        </div>
        <span :class="analysis.status === 'completed' ? 'data-badge' : 'coming-soon'">
          {{ analysis.status === "completed" ? "ANALYSIS COMPLETE" : "ANALYSIS FAILED" }}
        </span>
      </div>
      <div class="analysis-columns">
        <article class="analysis-card">
          <h3>阶段一 · 市场诊断</h3>
          <div class="analysis-row"><span>市场周期</span><strong>{{ diagnosis.cycle_position || "--" }}</strong></div>
          <div class="analysis-row"><span>方向</span><strong>{{ diagnosis.direction || "--" }}</strong></div>
          <div class="analysis-row"><span>闸门结果</span><strong>{{ diagnosis.gate_result || "--" }}</strong></div>
          <div class="analysis-row"><span>风险警示</span><strong>{{ diagnosis.risk_warning || "--" }}</strong></div>
          <p v-if="diagnosis.market_summary" class="analysis-text">{{ diagnosis.market_summary }}</p>
        </article>
        <article class="analysis-card">
          <h3>阶段二 · 交易决策</h3>
          <div class="analysis-row"><span>方向</span><strong>{{ decision.order_direction || "不下单" }}</strong></div>
          <div class="analysis-row"><span>订单类型</span><strong>{{ decision.order_type || "--" }}</strong></div>
          <div class="analysis-row"><span>入场价格</span><strong>{{ decision.entry_price ?? "--" }}</strong></div>
          <div class="analysis-row"><span>止损价格</span><strong>{{ decision.stop_loss_price ?? "--" }}</strong></div>
          <div class="analysis-row"><span>第一止盈</span><strong>{{ decision.take_profit_price ?? "--" }}</strong></div>
          <p v-if="decision.reasoning" class="analysis-text">{{ decision.reasoning }}</p>
        </article>
      </div>
      <div class="analysis-footer">
        <span>本次 Token：{{ analysis.usage.total_tokens ?? 0 }}</span>
        <span>今日剩余额度：{{ analysis.quota_remaining }}</span>
        <span>分析记录 ID：{{ analysis.analysis_id.slice(0, 8) }}</span>
      </div>
    </section>

    <section class="content-grid">
      <article class="chart-panel panel">
        <div class="panel-heading">
          <div>
            <h2>{{ symbol }} <span class="muted">{{ market === "spot" ? "SPOT" : "USDT PERPETUAL" }}</span></h2>
            <p>{{ interval }} · {{ exchangeLabel }} · K 线数据</p>
          </div>
          <span class="data-badge">REST MARKET DATA</span>
        </div>
        <div ref="chartHost" class="chart-host"></div>
        <div class="chart-legend">
          <span><i class="legend-green"></i>上涨</span>
          <span><i class="legend-red"></i>下跌</span>
          <span>已获取 {{ klines.length }} 根 K 线</span>
        </div>
      </article>

      <aside class="side-column">
        <article class="panel info-panel">
          <div class="panel-heading"><h2>行情提供商</h2><span class="coming-soon">多源适配</span></div>
          <div class="module-row"><span>Binance Vision</span><b>现货</b></div>
          <div class="module-row"><span>Binance.US</span><b>现货</b></div>
          <div class="module-row"><span>Coinbase Exchange</span><b>现货</b></div>
          <div class="module-row"><span>Gate.io</span><b>USDT 永续 · 已本地验证</b></div>
          <div class="module-row"><span>Binance USD-M</span><b>当前网络 451</b></div>
        </article>
        <article class="panel info-panel">
          <div class="panel-heading"><h2>24 小时成交</h2></div>
          <div class="metric-line"><span>基础资产成交量</span><strong>{{ formatNumber(ticker?.volume) }}</strong></div>
          <div class="metric-line"><span>计价资产成交额</span><strong>{{ formatNumber(ticker?.quote_volume) }}</strong></div>
          <p class="notice">不同交易所的交易对、计价货币和成交量单位可能不同。Gate K 线成交量已使用 quanto_multiplier 从合约张数换算为基础资产数量；原始合约张数单独保留。当前仅读取公开行情，不连接账户或执行交易。</p>
        </article>
      </aside>
    </section>

    <footer>PA Crypto Agent · Public market data · Not investment advice</footer>
  </main>
</template>
