<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import {
  api,
  type AnalysisComparison,
  type AnalysisHistoryDetail,
  type AnalysisHistoryItem,
  type Exchange,
  type Market,
  type SearchHistoryItem,
  type SegmentSample,
  type DatasetStats,
  type BatchBackfillJob,
  type MarketCandleArchiveStats,
} from "./api";

const props = defineProps<{
  market: Market;
  exchange: Exchange;
  symbol: string;
  timeframe: string;
}>();
const emit = defineEmits<{
  close: [];
  "restore-search": [item: SearchHistoryItem];
  "select-analysis": [detail: AnalysisHistoryDetail];
}>();

type Tab = "analyses" | "searches" | "dataset";
const activeTab = ref<Tab>("analyses");
const analyses = ref<AnalysisHistoryItem[]>([]);
const searches = ref<SearchHistoryItem[]>([]);
const selectedAnalysis = ref<AnalysisHistoryDetail | null>(null);
const selectedIds = ref<string[]>([]);
const comparison = ref<AnalysisComparison | null>(null);
const stats = ref<DatasetStats | null>(null);
const samples = ref<SegmentSample[]>([]);
const similar = ref<SegmentSample[]>([]);
const candleArchive = ref<MarketCandleArchiveStats | null>(null);
const batchSymbolsInput = ref("");
const batchTimeframes = ref<string[]>(["15m", "1h", "4h"]);
const batchBars = ref(3000);
const batchJob = ref<BatchBackfillJob | null>(null);
const batching = ref(false);
let batchPollTimer: ReturnType<typeof setTimeout> | undefined;
const loading = ref(false);
const building = ref(false);
const backfilling = ref(false);
const evaluating = ref(false);
const backfillCount = ref(3000);
const evaluation = ref<{
  status: string; train_count: number; test_count: number;
  rule_baseline?: { accuracy: number; balanced_accuracy: number; macro_f1: number };
  train_majority_baseline?: { accuracy: number; balanced_accuracy: number; macro_f1: number };
  note: string;
} | null>(null);
const error = ref("");
const notice = ref("");

const datasetQuery = computed(() => ({
  market: props.market,
  exchange: props.exchange,
  symbol: props.symbol,
  timeframe: props.timeframe,
}));

const batchTimeframeOptions = computed(() => {
  if (props.exchange === "coinbase") return ["1m", "5m", "15m", "1h", "6h", "1d"];
  if (props.exchange === "gate") return ["1m", "5m", "15m", "30m", "1h", "4h", "8h", "1d", "7d"];
  return ["1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d", "3d", "1w"];
});
const defaultBatchSymbols = computed(() =>
  props.exchange === "coinbase"
    ? "BTC-USD, ETH-USD"
    : "BTCUSDT, ETHUSDT, NEARUSDT",
);
const batchProgress = computed(() =>
  batchJob.value && batchJob.value.total > 0
    ? Math.round((batchJob.value.completed / batchJob.value.total) * 100)
    : 0,
);

watch(
  () => [props.market, props.exchange],
  () => {
    batchSymbolsInput.value = "";
    batchTimeframes.value = props.exchange === "coinbase"
      ? ["15m", "1h", "6h"]
      : ["15m", "1h", "4h"];
    batchJob.value = null;
  },
);

function formatTime(value: string | number | null | undefined) {
  if (!value) return "--";
  const date = typeof value === "number" ? new Date(value) : new Date(value);
  return Number.isNaN(date.getTime()) ? "--" : date.toLocaleString();
}
function formatNumber(value: unknown) {
  if (value === null || value === undefined || value === "") return "--";
  const n = Number(value);
  return Number.isFinite(n) ? n.toLocaleString("en-US", { maximumFractionDigits: 4 }) : String(value);
}
function statusLabel(value: string) {
  return value === "completed" ? "完成" : value === "failed" ? "失败" : value === "queued" ? "排队中" : value === "interrupted" ? "服务重启中断" : "进行中";
}
function toggleCompare(id: string) {
  if (selectedIds.value.includes(id)) {
    selectedIds.value = selectedIds.value.filter((value) => value !== id);
  } else if (selectedIds.value.length < 2) {
    selectedIds.value = [...selectedIds.value, id];
  } else {
    selectedIds.value = [selectedIds.value[1], id];
  }
  comparison.value = null;
}

async function refreshHistory() {
  loading.value = true;
  error.value = "";
  try {
    const [analysisResult, searchResult, datasetResult, sampleResult, candleResult] = await Promise.all([
      api.listAnalysisHistory(100),
      api.listSearches(100),
      api.datasetStats(datasetQuery.value),
      api.listDatasetSamples({ ...datasetQuery.value, limit: 100 }),
      api.marketCandleStats(datasetQuery.value),
    ]);
    analyses.value = analysisResult.items;
    searches.value = searchResult.items;
    stats.value = datasetResult;
    samples.value = sampleResult.items;
    candleArchive.value = candleResult.items[0] ?? null;
  } catch (err) {
    error.value = err instanceof Error ? err.message : "读取历史记录失败";
  } finally {
    loading.value = false;
  }
}

async function openAnalysis(item: AnalysisHistoryItem) {
  error.value = "";
  try {
    selectedAnalysis.value = await api.getAnalysisHistory(item.id);
  } catch (err) {
    error.value = err instanceof Error ? err.message : "读取分析详情失败";
  }
}

function showAnalysisOnChart() {
  if (selectedAnalysis.value) {
    emit("select-analysis", selectedAnalysis.value);
    emit("close");
  }
}

async function compareSelected() {
  if (selectedIds.value.length !== 2) return;
  error.value = "";
  try {
    comparison.value = await api.compareAnalysisHistory(selectedIds.value[0], selectedIds.value[1]);
  } catch (err) {
    error.value = err instanceof Error ? err.message : "比较分析结果失败";
  }
}

async function removeAnalysis(id: string) {
  if (!window.confirm("确定删除这条分析历史吗？")) return;
  try {
    await api.deleteAnalysisHistory(id);
    if (selectedAnalysis.value?.analysis_id === id) selectedAnalysis.value = null;
    analyses.value = analyses.value.filter((item) => item.id !== id);
    selectedIds.value = selectedIds.value.filter((value) => value !== id);
    notice.value = "分析记录已删除。";
  } catch (err) {
    error.value = err instanceof Error ? err.message : "删除分析记录失败";
  }
}

async function removeSearch(id: number) {
  try {
    await api.deleteSearch(id);
    searches.value = searches.value.filter((item) => item.id !== id);
  } catch (err) {
    error.value = err instanceof Error ? err.message : "删除搜索记录失败";
  }
}

async function buildDataset() {
  building.value = true;
  error.value = "";
  notice.value = "";
  try {
    const result = await api.buildHistoricalDataset({
      ...datasetQuery.value,
      limit: props.exchange === "coinbase" ? 300 : 1000,
    });
    stats.value = result.dataset;
    evaluation.value = null;
    similar.value = [];
    const [refreshed, archive] = await Promise.all([
      api.listDatasetSamples({ ...datasetQuery.value, limit: 100 }),
      api.marketCandleStats(datasetQuery.value),
    ]);
    samples.value = refreshed.items;
    candleArchive.value = archive.items[0] ?? null;
    notice.value = `已读取 ${result.received_bars} 根 K 线，新增 ${result.new_samples} 个历史样本。`;
  } catch (err) {
    error.value = err instanceof Error ? err.message : "构建历史样本失败";
  } finally {
    building.value = false;
  }
}

async function backfillHistory() {
  backfilling.value = true;
  error.value = "";
  notice.value = "";
  try {
    const result = await api.backfillDataset({
      market: props.market,
      exchange: props.exchange,
      symbol: props.symbol,
      timeframe: props.timeframe,
      bars: backfillCount.value,
    });
    stats.value = result.dataset;
    notice.value = `已回补 ${result.received_bars} 根已收盘 K 线，共 ${result.pages} 页，缺口 ${result.gaps} 处，新增 ${result.new_samples} 个样本；归档总计 ${result.archive_candles} 根。` +
      (result.truncated ? " 数据源可用历史不足或已达到分页上限。" : "");
    const [refreshed, archive] = await Promise.all([
      api.listDatasetSamples({ ...datasetQuery.value, limit: 100 }),
      api.marketCandleStats(datasetQuery.value),
    ]);
    samples.value = refreshed.items;
    candleArchive.value = archive.items[0] ?? null;
    evaluation.value = null;
    similar.value = [];
  } catch (err) {
    error.value = err instanceof Error ? err.message : "历史回补失败";
  } finally {
    backfilling.value = false;
  }
}

async function pollBatchBackfill(jobId: string) {
  try {
    const job = await api.getBatchBackfill(jobId);
    batchJob.value = job;
    if (job.status === "queued" || job.status === "running") {
      batchPollTimer = setTimeout(() => void pollBatchBackfill(jobId), 1200);
      return;
    }
    batching.value = false;
    await refreshHistory();
    const successful = job.results.filter((item) => item.status === "completed").length;
    const failed = job.results.length - successful;
    notice.value = `批量采集完成：成功 ${successful}/${job.total}，失败 ${failed}。已归档数据可在下次任务中续传。`;
  } catch (err) {
    batching.value = false;
    error.value = err instanceof Error ? err.message : "读取批量回补进度失败";
  }
}

async function startBatchBackfill() {
  error.value = "";
  notice.value = "";
  const symbols = (batchSymbolsInput.value.trim() || defaultBatchSymbols.value)
    .split(/[,，;；\\s]+/)
    .map((item) => item.trim().toUpperCase())
    .filter(Boolean);
  const timeframes = [...new Set(batchTimeframes.value)];
  if (!symbols.length) {
    error.value = "请至少输入一个交易对。";
    return;
  }
  if (!timeframes.length) {
    error.value = "请至少选择一个 K 线周期。";
    return;
  }
  const jobs = symbols.flatMap((item) =>
    timeframes.map((period) => ({
      market: props.market,
      exchange: props.exchange,
      symbol: item,
      timeframe: period,
      bars: batchBars.value,
    })),
  );
  if (jobs.length > 24) {
    error.value = "单批最多支持 24 个交易对与周期组合。";
    return;
  }
  if (jobs.length * batchBars.value > 60000) {
    error.value = "单批回补总量不能超过 60000 根 K 线，请减少组合或回补数量。";
    return;
  }
  batching.value = true;
  batchJob.value = null;
  if (batchPollTimer) clearTimeout(batchPollTimer);
  try {
    const job = await api.startBatchBackfill(jobs);
    batchJob.value = job;
    void pollBatchBackfill(job.job_id);
  } catch (err) {
    batching.value = false;
    error.value = err instanceof Error ? err.message : "启动批量历史采集失败";
  }
}

onBeforeUnmount(() => {
  if (batchPollTimer) clearTimeout(batchPollTimer);
});

async function evaluateBaseline() {
  evaluating.value = true;
  error.value = "";
  try {
    evaluation.value = await api.evaluateDataset(datasetQuery.value);
    notice.value = evaluation.value.note;
  } catch (err) {
    error.value = err instanceof Error ? err.message : "基线评估失败";
  } finally {
    evaluating.value = false;
  }
}

async function loadSimilar() {
  error.value = "";
  try {
    const result = await api.similarSegments({ ...datasetQuery.value, limit: 5 });
    similar.value = result.items;
    notice.value = result.note;
  } catch (err) {
    error.value = err instanceof Error ? err.message : "相似行情检索失败";
  }
}

onMounted(() => void refreshHistory());
</script>

<template>
  <div class="history-drawer-backdrop" @click.self="emit('close')">
    <aside class="history-drawer" role="dialog" aria-modal="true" aria-label="历史记录">
      <header class="history-drawer-header">
        <div>
          <div class="eyebrow">PA HISTORY & DATASET</div>
          <h2>历史记录与样本库</h2>
          <p>{{ symbol }} · {{ exchange }} · {{ timeframe }} · 历史记录保存在本地 SQLite</p>
        </div>
        <button class="analysis-drawer-close" aria-label="关闭历史记录" @click="emit('close')">×</button>
      </header>

      <nav class="analysis-tabs history-tabs">
        <button :class="{ active: activeTab === 'analyses' }" @click="activeTab = 'analyses'">分析历史</button>
        <button :class="{ active: activeTab === 'searches' }" @click="activeTab = 'searches'">搜索历史</button>
        <button :class="{ active: activeTab === 'dataset' }" @click="activeTab = 'dataset'">行情样本库</button>
      </nav>

      <div class="history-drawer-scroll">
        <div v-if="error" class="ai-inline-error">{{ error }}</div>
        <div v-if="notice" class="ai-inline-notice">{{ notice }}</div>
        <div v-if="loading" class="analysis-empty-state compact">正在读取本地历史记录…</div>

        <template v-else-if="activeTab === 'analyses'">
          <div class="history-section-heading">
            <div>
              <h3>AI 分析记录</h3>
              <p>保留每次分析的结构化结果、流程轨迹及 Prompt/策略文件哈希。</p>
            </div>
            <button class="secondary-button" @click="refreshHistory">刷新</button>
          </div>
          <div v-if="analyses.length" class="history-analysis-list">
            <article v-for="item in analyses" :key="item.id" class="history-analysis-item">
              <div class="history-analysis-main" @click="openAnalysis(item)">
                <div class="history-analysis-title">
                  <strong>{{ item.symbol }} · {{ item.timeframe }}</strong>
                  <span class="analysis-status" :class="item.status">{{ statusLabel(item.status) }}</span>
                </div>
                <p>{{ item.exchange }} · {{ item.market }} · {{ formatTime(item.created_at) }}</p>
                <div class="history-analysis-tags">
                  <span>{{ item.cycle_position || "未完成诊断" }}</span>
                  <span>{{ item.direction || "--" }}</span>
                  <span>{{ item.gate_result || "--" }}</span>
                  <span>{{ item.order_type || "--" }}</span>
                </div>
              </div>
              <div class="history-analysis-actions">
                <label class="history-compare-check">
                  <input
                    type="checkbox"
                    :checked="selectedIds.includes(item.id)"
                    @change="toggleCompare(item.id)"
                  />
                  <span>比较</span>
                </label>
                <button class="text-action" @click="openAnalysis(item)">详情</button>
                <button class="text-action danger" @click="removeAnalysis(item.id)">删除</button>
              </div>
            </article>
            <div class="history-compare-bar">
              <span>已选 {{ selectedIds.length }}/2 条记录</span>
              <button class="secondary-button" :disabled="selectedIds.length !== 2" @click="compareSelected">比较差异</button>
            </div>
          </div>
          <div v-else class="analysis-empty-state compact">还没有分析历史。完成一次 AI 分析后会自动保存。</div>

          <section v-if="comparison" class="history-subsection">
            <h3>结构化结果比较</h3>
            <p>{{ comparison.left.analysis_id.slice(0, 8) }} ↔ {{ comparison.right.analysis_id.slice(0, 8) }}</p>
            <div v-if="Object.keys(comparison.differences).length" class="history-diff-list">
              <div v-for="(change, key) in comparison.differences" :key="key" class="history-diff-row">
                <strong>{{ key }}</strong>
                <span>{{ formatNumber(change.left) }}</span>
                <span>→</span>
                <span>{{ formatNumber(change.right) }}</span>
              </div>
            </div>
            <div v-else class="analysis-empty-state compact">两次分析的核心结构化字段没有变化。</div>
          </section>

          <section v-if="selectedAnalysis" class="history-subsection">
            <div class="history-section-heading">
              <div>
                <h3>分析详情与版本溯源</h3>
                <p>{{ selectedAnalysis.analysis_id }}</p>
              </div>
              <button class="secondary-button" @click="showAnalysisOnChart">在当前图表显示关键位</button>
            </div>
            <div class="history-provenance">
              <strong>实际使用的 Prompt / 策略文件</strong>
              <div
                v-for="file in selectedAnalysis.provenance?.prompt_files || []"
                :key="file.path"
                class="history-file-row"
              >
                <span>{{ file.path }}</span>
                <code>{{ file.sha256?.slice(0, 16) || "missing" }}</code>
              </div>
              <strong>PA 核心引擎版本</strong>
              <div
                v-for="file in selectedAnalysis.provenance?.engine_files || []"
                :key="file.path"
                class="history-file-row"
              >
                <span>{{ file.path }}</span>
                <code>{{ file.sha256?.slice(0, 16) || "missing" }}</code>
              </div>
            </div>
            <details class="history-json-details">
              <summary>查看完整结构化分析结果</summary>
              <pre class="analysis-json">{{ JSON.stringify(selectedAnalysis.result, null, 2) }}</pre>
            </details>
          </section>
        </template>

        <template v-else-if="activeTab === 'searches'">
          <div class="history-section-heading">
            <div>
              <h3>最近搜索</h3>
              <p>点击记录可恢复交易所、交易对和周期。</p>
            </div>
            <button class="secondary-button" @click="refreshHistory">刷新</button>
          </div>
          <div v-if="searches.length" class="history-search-list">
            <article v-for="item in searches" :key="item.id" class="history-search-item">
              <button class="history-search-restore" @click="emit('restore-search', item)">
                <strong>{{ item.symbol }}</strong>
                <span>{{ item.exchange }} · {{ item.market }} · {{ item.timeframe }}</span>
                <small>{{ formatTime(item.created_at) }}</small>
              </button>
              <button class="text-action danger" @click="removeSearch(item.id)">删除</button>
            </article>
          </div>
          <div v-else class="analysis-empty-state compact">暂无搜索记录。手动点击「查询行情」后会自动保存。</div>
        </template>

        <template v-else>
          <div class="history-section-heading">
            <div>
              <h3>历史行情样本</h3>
              <p>当前范围：{{ symbol }} · {{ exchange }} · {{ timeframe }}</p>
            </div>
            <button class="secondary-button" :disabled="building" @click="buildDataset">
              {{ building ? "构建中…" : "构建历史样本" }}
            </button>
          </div>
          <div class="history-dataset-actions">
            <label class="history-backfill-count">
              <span>回补 K 线</span>
              <select v-model.number="backfillCount">
                <option :value="1000">1000 根</option>
                <option :value="3000">3000 根</option>
                <option :value="6000">6000 根</option>
                <option :value="12000">12000 根</option>
              </select>
            </label>
            <button class="secondary-button" :disabled="backfilling" @click="backfillHistory">
              {{ backfilling ? "历史分页回补中…" : "历史分页回补" }}
            </button>
          </div>
          <div class="history-dataset-actions">
            <button class="secondary-button" :disabled="evaluating" @click="evaluateBaseline">
              {{ evaluating ? "评估中…" : "评估固定规则基线" }}
            </button>
          </div>
          <section v-if="evaluation" class="history-subsection">
            <h3>独立测试集表现（非 AI 两阶段准确率）</h3>
            <p>训练样本 {{ evaluation.train_count }} · 测试样本 {{ evaluation.test_count }}</p>
            <div v-if="evaluation.status === 'evaluated'" class="history-dataset-metrics">
              <div><small>固定规则准确率</small><strong>{{ ((evaluation.rule_baseline?.accuracy ?? 0) * 100).toFixed(1) }}%</strong></div>
              <div><small>训练集多数类基线</small><strong>{{ ((evaluation.train_majority_baseline?.accuracy ?? 0) * 100).toFixed(1) }}%</strong></div>
              <div><small>规则 Macro F1</small><strong>{{ (evaluation.rule_baseline?.macro_f1 ?? 0).toFixed(3) }}</strong></div>
            </div>
            <p class="history-dataset-note">{{ evaluation.note }}</p>
          </section>
          <div v-if="stats" class="history-dataset-metrics">
            <div><small>样本数</small><strong>{{ stats.sample_count }}</strong></div>
            <div><small>特征版本</small><strong>{{ stats.feature_schema_version }}</strong></div>
            <div><small>标签版本</small><strong>{{ stats.label_schema_version }}</strong></div>
          </div>
          <div v-if="stats" class="history-distributions">
            <div>
              <strong>行情类别</strong>
              <span v-for="(count, key) in stats.regime_distribution" :key="key">{{ key }}：{{ count }}</span>
            </div>
            <div>
              <strong>未来结果标签</strong>
              <span v-for="(count, key) in stats.label_distribution" :key="key">{{ key }}：{{ count }}</span>
            </div>
            <div>
              <strong>时间切分</strong>
              <span v-for="(count, key) in stats.split_distribution" :key="key">{{ key }}：{{ count }}</span>
            </div>
          </div>
          <p v-if="stats" class="history-dataset-note">{{ stats.note }}</p>
          <div class="history-dataset-actions">
            <button class="secondary-button" @click="loadSimilar">检索当前行情的相似历史片段</button>
            <button class="text-action" @click="refreshHistory">刷新样本</button>
          </div>
          <section v-if="similar.length" class="history-subsection">
            <h3>相似历史片段</h3>
            <article v-for="item in similar" :key="item.sample_id" class="history-sample-item">
              <div><strong>{{ item.regime }}</strong><span>相似度 {{ (item.similarity! * 100).toFixed(1) }}%</span></div>
              <p>{{ formatTime(item.decision_time_ms) }} · 未来 {{ item.labels.forward_direction }} · {{ formatNumber(item.labels.forward_return_pct) }}%</p>
              <small>标签只用于事后评估，不作为当前预测特征。</small>
            </article>
          </section>
          <section class="history-subsection">
            <h3>最近样本（{{ samples.length }}）</h3>
            <article v-for="item in samples" :key="item.sample_id" class="history-sample-item">
              <div><strong>{{ item.regime }}</strong><span>{{ item.split }}</span></div>
              <p>{{ formatTime(item.decision_time_ms) }} · 未来 {{ item.labels.forward_direction }} · {{ formatNumber(item.labels.forward_return_pct) }}%</p>
            </article>
            <div v-if="!samples.length" class="analysis-empty-state compact">还没有样本。点击「构建历史样本」获取最近 K 线并生成数据集。</div>
          </section>
          <p class="history-dataset-note">
            特征只使用决策时点及之前的已收盘 K 线；未来 12 根 K 线只用于生成独立 labels。当前仅建立数据集和相似度检索，尚未训练或微调 AI 模型。
          </p>
        </template>
      </div>
    </aside>
  </div>
</template>
