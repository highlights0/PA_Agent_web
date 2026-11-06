<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { ColorType, createChart, type IChartApi, type ISeriesApi } from "lightweight-charts";
import { api, type AnalysisResult, type Exchange, type Kline, type Market, type Ticker } from "./api";

const market = ref<Market>("spot");
const exchange = ref<Exchange>("coinbase");
const symbol = ref("BTC-USD");
const interval = ref("15m");
const klines = ref<Kline[]>([]);
const ticker = ref<Ticker | null>(null);
const analysis = ref<AnalysisResult | null>(null);
const loading = ref(false);
const analysisLoading = ref(false);
const error = ref("");
const analysisError = ref("");
const chartHost = ref<HTMLDivElement | null>(null);
let chart: IChartApi | null = null;
let candleSeries: ISeriesApi<"Candlestick"> | null = null;
let resizeObserver: ResizeObserver | null = null;
let refreshTimer: number | undefined;

const changePositive = computed(() => Number(ticker.value?.price_change_percent ?? 0) >= 0);
const diagnosis = computed<Record<string, any>>(() => analysis.value?.stage1_diagnosis ?? {});
const decision = computed<Record<string, any>>(() => analysis.value?.stage2_decision?.decision ?? {});
const exchangeLabel = computed(() => ({
  binance: "Binance Global",
  binance_us: "Binance.US",
  coinbase: "Coinbase Exchange",
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
    if (!/^[A-Z0-9-]{3,24}$/.test(normalized)) throw new Error("交易对格式不正确");
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

function onMarketChange() {
  analysis.value = null;
  if (market.value === "usdm") {
    exchange.value = "binance";
    if (symbol.value.includes("-")) symbol.value = "BTCUSDT";
  } else if (exchange.value === "binance") {
    exchange.value = "coinbase";
    if (!symbol.value.includes("-")) symbol.value = "BTC-USD";
  }
  void loadMarket();
}

function onExchangeChange() {
  analysis.value = null;
  if (market.value === "usdm") market.value = "spot";
  if (exchange.value === "coinbase" && !symbol.value.includes("-")) symbol.value = "BTC-USD";
  if (exchange.value !== "coinbase" && symbol.value.includes("-")) symbol.value = "BTCUSDT";
  void loadMarket();
}

function formatNumber(value: string | undefined) {
  if (!value) return "--";
  return Number(value).toLocaleString("en-US", { maximumFractionDigits: 6 });
}

watch(interval, () => {
  analysis.value = null;
  void loadMarket();
});
onMounted(() => {
  void loadMarket();
  refreshTimer = window.setInterval(() => void loadMarket(), 15_000);
});
onBeforeUnmount(() => {
  if (refreshTimer) window.clearInterval(refreshTimer);
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
        <p>多交易所现货行情 · U 本位合约独立数据源 · 原版 Price Action 两阶段分析</p>
      </div>
      <div class="refresh-state">{{ loading ? "正在同步…" : "每 15 秒刷新" }}</div>
    </section>

    <section class="toolbar panel">
      <label>
        <span>市场</span>
        <select v-model="market" @change="onMarketChange">
          <option value="spot">现货 Spot</option>
          <option value="usdm">U 本位合约</option>
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
        <select disabled><option>Binance USDⓈ-M</option></select>
      </label>
      <label class="symbol-field">
        <span>交易对</span>
        <input v-model="symbol" spellcheck="false" @keyup.enter="loadMarket" />
      </label>
      <label>
        <span>周期</span>
        <select v-model="interval">
          <option v-for="tf in ['1m','5m','15m','30m','1h','4h','1d']" :key="tf" :value="tf">{{ tf }}</option>
        </select>
      </label>
      <button class="secondary-button" :disabled="loading" @click="loadMarket">查询行情</button>
      <button class="primary-button" :disabled="analysisLoading" @click="runAnalysis">
        {{ analysisLoading ? "AI 分析中…" : "AI 两阶段分析" }}
      </button>
    </section>

    <div v-if="market === 'usdm' && exchange === 'binance'" class="notice-banner">
      当前合约行情仍使用 Binance Global。若该网络返回 HTTP 451，切换到现货并选择 Coinbase 或 Binance.US；美国合规期货数据需要单独接入 Coinbase Derivatives 的 FIX/UDP 市场数据服务，不能用现货行情冒充永续合约。
    </div>
    <div v-if="error" class="error-banner">{{ error }}</div>
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
            <h2>{{ symbol }} <span class="muted">{{ market === "spot" ? "SPOT" : "USDⓈ-M" }}</span></h2>
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
          <div class="module-row"><span>Binance Global</span><b>现货 / USD-M</b></div>
          <div class="module-row"><span>Binance.US</span><b>现货</b></div>
          <div class="module-row"><span>Coinbase Exchange</span><b>现货</b></div>
          <div class="module-row"><span>Coinbase Derivatives</span><b>FIX/UDP 待接入</b></div>
        </article>
        <article class="panel info-panel">
          <div class="panel-heading"><h2>24 小时成交</h2></div>
          <div class="metric-line"><span>基础资产成交量</span><strong>{{ formatNumber(ticker?.volume) }}</strong></div>
          <div class="metric-line"><span>计价资产成交额</span><strong>{{ formatNumber(ticker?.quote_volume) }}</strong></div>
          <p class="notice">各交易所交易对和计价货币不同，例如 Coinbase 使用 BTC-USD，Binance 使用 BTCUSDT。不可将不同市场的 OHLCV 直接拼接。此版本仅读取公开行情，不连接账户或执行交易。</p>
        </article>
      </aside>
    </section>

    <footer>PA Crypto Agent · Public market data · Not investment advice</footer>
  </main>
</template>
