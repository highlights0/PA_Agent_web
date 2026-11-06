<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { ColorType, createChart, type IChartApi, type ISeriesApi } from "lightweight-charts";
import { api, type Kline, type Market, type Ticker } from "./api";

const market = ref<Market>("usdm");
const symbol = ref("BTCUSDT");
const interval = ref("15m");
const klines = ref<Kline[]>([]);
const ticker = ref<Ticker | null>(null);
const loading = ref(false);
const error = ref("");
const chartHost = ref<HTMLDivElement | null>(null);
let chart: IChartApi | null = null;
let candleSeries: ISeriesApi<"Candlestick"> | null = null;
let resizeObserver: ResizeObserver | null = null;
let refreshTimer: number | undefined;

const changePositive = computed(() => Number(ticker.value?.price_change_percent ?? 0) >= 0);

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
    if (!/^[A-Z0-9]{3,24}$/.test(normalized)) throw new Error("交易对格式不正确");
    symbol.value = normalized;
    const [bars, quote] = await Promise.all([
      api.klines(market.value, normalized, interval.value, 200),
      api.ticker(market.value, normalized),
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

function formatNumber(value: string | undefined) {
  if (!value) return "--";
  return Number(value).toLocaleString("en-US", { maximumFractionDigits: 6 });
}

watch([market, interval], () => void loadMarket());
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
      <div class="connection"><span class="live-dot"></span> Binance 公共行情</div>
    </header>

    <section class="page-heading">
      <div>
        <div class="eyebrow">MARKET OVERVIEW</div>
        <h1>加密货币行情中心</h1>
        <p>现货与 U 本位合约行情 · 后续接入原版 Price Action 两阶段分析</p>
      </div>
      <div class="refresh-state">{{ loading ? "正在同步…" : "每 15 秒刷新" }}</div>
    </section>

    <section class="toolbar panel">
      <label>
        <span>市场</span>
        <select v-model="market">
          <option value="spot">现货 Spot</option>
          <option value="usdm">U 本位合约</option>
        </select>
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
      <button class="primary-button" :disabled="loading" @click="loadMarket">查询行情</button>
    </section>

    <div v-if="error" class="error-banner">{{ error }}</div>

    <section class="stats-grid">
      <article class="stat-card panel">
        <span class="stat-label">最新价格</span>
        <strong class="price-value">{{ formatNumber(ticker?.last_price) }}</strong>
        <span class="stat-foot">{{ symbol }}</span>
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

    <section class="content-grid">
      <article class="chart-panel panel">
        <div class="panel-heading">
          <div>
            <h2>{{ symbol }} <span class="muted">{{ market === "spot" ? "SPOT" : "USDⓈ-M" }}</span></h2>
            <p>{{ interval }} · Binance · K 线数据</p>
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
          <div class="panel-heading"><h2>分析模块</h2><span class="coming-soon">开发中</span></div>
          <div class="module-row"><span>Price Action Stage 1</span><b>待接入</b></div>
          <div class="module-row"><span>Price Action Stage 2</span><b>待接入</b></div>
          <div class="module-row"><span>合约资金费率 / OI</span><b>API 已建立</b></div>
          <div class="module-row"><span>持续监控与机会提醒</span><b>后续阶段</b></div>
        </article>
        <article class="panel info-panel">
          <div class="panel-heading"><h2>24 小时成交</h2></div>
          <div class="metric-line"><span>基础资产成交量</span><strong>{{ formatNumber(ticker?.volume) }}</strong></div>
          <div class="metric-line"><span>计价资产成交额</span><strong>{{ formatNumber(ticker?.quote_volume) }}</strong></div>
          <p class="notice">当前版本仅读取公开行情，不连接 Binance 用户账户，不包含下单、撤单或资金操作。</p>
        </article>
      </aside>
    </section>

    <footer>PA Crypto Agent · Public market data · Not investment advice</footer>
  </main>
</template>
