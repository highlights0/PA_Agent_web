<script setup lang="ts">
import { computed, ref } from "vue";
import type { AnalysisJobSnapshot, AnalysisTraceEvent } from "./api";

const props = defineProps<{ job: AnalysisJobSnapshot | null }>();
const emit = defineEmits<{
  close: [];
  "highlight-level": [level: string, kind: "support" | "resistance"];
  "highlight-bars": [barFrom: number, barTo: number];
}>();

type Tab = "timeline" | "stage1" | "validation" | "stage2" | "market" | "provenance" | "trace";
const activeTab = ref<Tab>("timeline");

const tabs: Array<{ id: Tab; label: string }> = [
  { id: "timeline", label: "流程时间线" },
  { id: "stage1", label: "阶段一" },
  { id: "validation", label: "校验与重试" },
  { id: "stage2", label: "阶段二" },
  { id: "market", label: "输入数据" },
  { id: "provenance", label: "版本溯源" },
  { id: "trace", label: "调试记录" },
];

const trace = computed(() => props.job?.trace ?? []);
const details = computed(() => props.job?.details ?? null);
const result = computed(() => props.job?.result ?? null);
const statusLabel = computed(() => {
  switch (props.job?.status) {
    case "queued": return "排队中";
    case "running": return "分析进行中";
    case "completed": return "已完成";
    case "failed": return "失败";
    default: return "等待分析";
  }
});
const statusClass = computed(() => props.job?.status ?? "idle");
const elapsedLabel = computed(() => {
  const first = trace.value[0]?.elapsed_ms ?? 0;
  const last = trace.value[trace.value.length - 1]?.elapsed_ms ?? first;
  return ((last - first) / 1000).toFixed(1) + "s";
});
const validationItems = computed(() =>
  trace.value.filter((event) =>
    event.type === "validation_passed" || event.type === "validation_failed"
  ),
);
const modelCalls = computed(() => details.value?.model_calls ?? trace.value
  .filter((event) => event.type === "model_call_completed" || event.type === "model_call_failed")
  .map((event) => event.data));
const stage1Output = computed(() => {
  if (details.value?.stage1_diagnosis) return details.value.stage1_diagnosis;
  const event = [...trace.value].reverse().find((item) =>
    item.type === "validation_passed" && item.stage === "stage1"
  );
  return event?.data?.validated_output ?? null;
});
const stage2Output = computed(() => {
  if (details.value?.stage2_decision) return details.value.stage2_decision;
  const event = [...trace.value].reverse().find((item) =>
    item.type === "validation_passed" && item.stage === "stage2"
  );
  return event?.data?.validated_output ?? null;
});
const stage1Prompt = computed(() => details.value?.prompts?.stage1 ?? trace.value
  .find((item) => item.type === "prompt_ready" && item.stage === "stage1")?.data ?? null);
const stage2Prompt = computed(() => details.value?.prompts?.stage2 ?? trace.value
  .find((item) => item.type === "prompt_ready" && item.stage === "stage2")?.data ?? null);
const stage1Diagnosis = computed(() => details.value?.stage1_diagnosis ?? stage1Output.value ?? {});
const chartFindings = computed(() => details.value?.chart_findings ?? {
  support_levels: stage1Diagnosis.value?.support_levels ?? [],
  resistance_levels: stage1Diagnosis.value?.resistance_levels ?? [],
  bar_by_bar_summary: stage1Diagnosis.value?.bar_by_bar_summary ?? [],
  gate_trace: stage1Diagnosis.value?.gate_trace ?? [],
});
const strategyFiles = computed(() => {
  if (details.value?.strategy_files_used) return details.value.strategy_files_used;
  const routed = [...trace.value].reverse().find((item) => item.type === "strategy_routed");
  return routed?.data?.strategy_files ?? [];
});
const frameInfo = computed(() => details.value?.frame
  ?? [...trace.value].reverse().find((item) => item.type === "frame_ready")?.data
  ?? null);
const marketContext = computed(() => details.value?.market_context
  ?? [...trace.value].reverse().find((item) => item.type === "derivatives_context_loaded")?.data
  ?? null);

function pretty(value: unknown) {
  return value == null ? "暂无数据" : JSON.stringify(value, null, 2);
}
function time(value: string | null | undefined) {
  if (!value) return "--";
  return new Date(value).toLocaleTimeString();
}
function elapsed(value: number | null | undefined) {
  return value == null ? "--" : (value / 1000).toFixed(1) + "s";
}
function formatNumber(value: unknown) {
  if (value == null || value === "") return "--";
  const number = Number(value);
  return Number.isFinite(number)
    ? number.toLocaleString("en-US", { maximumFractionDigits: 6 })
    : String(value);
}
function eventTone(event: AnalysisTraceEvent) {
  if (event.type.includes("failed") || event.type === "preflight_failed") return "failed";
  if (event.type.includes("retry")) return "retry";
  if (event.type.includes("done") || event.type.includes("completed") || event.type === "record_saved") return "done";
  return "normal";
}
function latestValidation(stage: string) {
  return [...validationItems.value].reverse().find((item) => item.stage === stage) ?? null;
}
function highlightBar(bar: unknown) {
  const match = String(bar ?? "").match(/^K(\d+)$/i);
  if (!match) return;
  const seq = Number(match[1]);
  emit("highlight-bars", seq, seq);
}
function highlightNode(node: Record<string, any>) {
  const from = Number(node.bar_from);
  const to = Number(node.bar_to);
  if (Number.isFinite(from) && Number.isFinite(to) && from > 0 && to > 0) {
    emit("highlight-bars", from, to);
    return;
  }
  const match = String(node.bar_range ?? "").match(/^K(\d+)(?:-K(\d+))?$/i);
  if (match) emit("highlight-bars", Number(match[1]), Number(match[2] ?? match[1]));
}
</script>

<template>
  <div class="analysis-drawer-backdrop" @click.self="emit('close')">
    <aside class="analysis-drawer" role="dialog" aria-modal="true" aria-label="分析过程">
      <header class="analysis-drawer-header">
        <div class="analysis-drawer-heading">
          <div class="eyebrow">PA ANALYSIS TRACE</div>
          <h2>分析过程</h2>
          <p v-if="job">
            {{ job.request.symbol }} · {{ job.request.exchange }} · {{ job.request.timeframe }}
            <span v-if="details?.provider?.model"> · {{ details.provider.model }}</span>
          </p>
          <p v-else>执行两阶段分析后，这里会显示完整流程。</p>
        </div>
        <div class="analysis-drawer-header-actions">
          <span class="analysis-status" :class="statusClass">{{ statusLabel }}</span>
          <button class="analysis-drawer-close" aria-label="关闭分析过程" @click="emit('close')">×</button>
        </div>
      </header>

      <div v-if="job" class="analysis-job-meta">
        <span>任务 {{ job.job_id.slice(0, 8) }}</span>
        <span>事件 {{ trace.length }}</span>
        <span>耗时 {{ elapsedLabel }}</span>
        <span>创建于 {{ time(job.created_at) }}</span>
      </div>

      <nav class="analysis-tabs" aria-label="分析过程菜单">
        <button
          v-for="tab in tabs"
          :key="tab.id"
          :class="{ active: activeTab === tab.id }"
          @click="activeTab = tab.id"
        >{{ tab.label }}</button>
      </nav>

      <div class="analysis-drawer-scroll">
        <div v-if="!job" class="analysis-empty-state">
          <strong>尚无分析记录</strong>
          <p>点击主页面的「AI 两阶段分析」后，这里会实时显示预检、模型请求、校验重试、策略路由和最终决策。</p>
        </div>

        <template v-else>
          <section v-if="activeTab === 'timeline'" class="analysis-tab-content">
            <div class="analysis-section-heading">
              <div>
                <h3>执行时间线</h3>
                <p>按原版编排器实际回调记录，不显示虚构进度。</p>
              </div>
              <span class="analysis-muted">{{ trace.length }} events</span>
            </div>
            <div v-if="trace.length" class="analysis-timeline">
              <article
                v-for="event in trace"
                :key="event.id"
                class="analysis-timeline-event"
                :class="eventTone(event)"
              >
                <span class="analysis-timeline-dot"></span>
                <div class="analysis-timeline-main">
                  <div class="analysis-timeline-topline">
                    <strong>{{ event.title }}</strong>
                    <time>{{ elapsed(event.elapsed_ms) }}</time>
                  </div>
                  <p v-if="event.detail">{{ event.detail }}</p>
                  <small>{{ time(event.timestamp) }}<span v-if="event.stage"> · {{ event.stage }}</span></small>
                </div>
              </article>
            </div>
            <div v-else class="analysis-empty-state compact">等待后端返回第一个流程事件…</div>
            <div v-if="job.error" class="analysis-error-detail">
              <strong>任务异常</strong>
              <p>{{ job.error.message }}</p>
              <small>HTTP {{ job.error.status_code }}</small>
            </div>
            <div v-if="result?.error" class="analysis-error-detail">
              <strong>分析结果异常</strong>
              <p>{{ result.error.message || pretty(result.error) }}</p>
            </div>
          </section>

          <section v-else-if="activeTab === 'stage1'" class="analysis-tab-content">
            <div class="analysis-section-heading">
              <div>
                <h3>阶段一 · 市场诊断</h3>
                <p>模型输出经过原版 JSON / 业务规则校验后的结构化结果。</p>
              </div>
              <span class="analysis-mini-status" :class="latestValidation('stage1')?.data?.status">
                {{ latestValidation("stage1")?.data?.status === "passed" ? "校验通过" : stage1Output ? "已生成" : "等待中" }}
              </span>
            </div>
            <div v-if="stage1Prompt" class="analysis-prompt-summary">
              <strong>Prompt 构建摘要</strong>
              <div class="analysis-prompt-grid">
                <span>System {{ formatNumber(stage1Prompt.system_chars) }} 字符</span>
                <span>User {{ formatNumber(stage1Prompt.user_chars) }} 字符</span>
              </div>
              <div v-if="stage1Prompt.system_headings?.length">
                <small>System 章节</small>
                <div class="analysis-chip-list">
                  <span v-for="heading in stage1Prompt.system_headings" :key="heading">{{ heading }}</span>
                </div>
              </div>
              <div v-if="stage1Prompt.user_headings?.length">
                <small>User 章节</small>
                <div class="analysis-chip-list">
                  <span v-for="heading in stage1Prompt.user_headings" :key="heading">{{ heading }}</span>
                </div>
              </div>
            </div>
            <div v-if="chartFindings.support_levels?.length || chartFindings.resistance_levels?.length" class="analysis-subsection">
              <h4>支撑 / 阻力价位 · 点击定位图表</h4>
              <div class="analysis-chip-list">
                <button
                  v-for="level in chartFindings.support_levels || []"
                  :key="'support-' + level"
                  class="analysis-click-chip support"
                  @click="emit('highlight-level', level, 'support')"
                >支撑 {{ level }}</button>
                <button
                  v-for="level in chartFindings.resistance_levels || []"
                  :key="'resistance-' + level"
                  class="analysis-click-chip resistance"
                  @click="emit('highlight-level', level, 'resistance')"
                >阻力 {{ level }}</button>
              </div>
            </div>
            <div v-if="chartFindings.bar_by_bar_summary?.length" class="analysis-subsection">
              <h4>逐 K 线摘要 · 点击定位</h4>
              <button
                v-for="item in chartFindings.bar_by_bar_summary"
                :key="item.bar"
                class="analysis-bar-link"
                @click="highlightBar(item.bar)"
              >
                <strong>{{ item.bar }} · {{ item.role }}</strong>
                <span>{{ item.reason }}</span>
              </button>
            </div>
            <div v-if="chartFindings.gate_trace?.length" class="analysis-subsection">
              <h4>闸门决策树 · 点击定位 K 线范围</h4>
              <button
                v-for="node in chartFindings.gate_trace"
                :key="node.node_id"
                class="analysis-bar-link"
                @click="highlightNode(node)"
              >
                <strong>{{ node.node_id }} · {{ node.question }}</strong>
                <span>{{ node.answer }} · {{ node.bar_range }} · {{ node.reason }}</span>
              </button>
            </div>
            <pre v-if="stage1Output" class="analysis-json">{{ pretty(stage1Output) }}</pre>
            <div v-else class="analysis-empty-state compact">阶段一结构化诊断尚未生成。</div>
          </section>

          <section v-else-if="activeTab === 'validation'" class="analysis-tab-content">
            <div class="analysis-section-heading">
              <div>
                <h3>JSON 校验与重试</h3>
                <p>展示原版验证器实际返回的类别、缺失字段和非法字段。</p>
              </div>
              <span class="analysis-muted">{{ validationItems.length }} 次校验</span>
            </div>
            <div v-if="validationItems.length" class="analysis-validation-list">
              <article
                v-for="event in validationItems"
                :key="event.id"
                class="analysis-validation-item"
                :class="event.type === 'validation_passed' ? 'passed' : 'rejected'"
              >
                <div class="analysis-validation-topline">
                  <strong>{{ event.stage === 'stage1' ? '阶段一' : '阶段二' }} · {{ event.type === 'validation_passed' ? '通过' : '未通过' }}</strong>
                  <span>第 {{ event.data.attempt }} 次</span>
                </div>
                <p>{{ event.data.message }}</p>
                <div v-if="event.data.category" class="analysis-validation-meta">
                  <span>类别 {{ event.data.category }}</span>
                  <span v-if="event.data.parse_position">位置 {{ event.data.parse_position }}</span>
                </div>
                <div v-if="event.data.missing_fields?.length" class="analysis-field-list">
                  <small>缺失字段</small>
                  <code v-for="field in event.data.missing_fields" :key="field">{{ field }}</code>
                </div>
                <div v-if="event.data.invalid_fields?.length" class="analysis-field-list">
                  <small>非法字段</small>
                  <code v-for="field in event.data.invalid_fields" :key="field">{{ field }}</code>
                </div>
              </article>
            </div>
            <div v-else class="analysis-empty-state compact">尚未发生 JSON 校验。模型返回后，验证器结果会出现在这里。</div>
            <div v-if="modelCalls.length" class="analysis-model-calls">
              <h4>模型调用记录</h4>
              <div v-for="call in modelCalls" :key="call.call" class="analysis-call-row">
                <span>{{ call.stage }} · 第 {{ call.call }} 次请求</span>
                <strong>{{ call.status === "completed" ? "成功" : call.status === "failed" ? "失败" : "进行中" }}</strong>
                <small>{{ formatNumber(call.total_tokens) }} tokens · {{ formatNumber(call.latency_ms) }} ms</small>
                <details v-if="call.output_text" class="analysis-output-details">
                  <summary>查看模型正文（不含隐藏推理）</summary>
                  <pre class="analysis-json">{{ call.output_text }}</pre>
                </details>
                <p v-if="call.error" class="analysis-inline-error">{{ call.error }}</p>
              </div>
            </div>
          </section>

          <section v-else-if="activeTab === 'stage2'" class="analysis-tab-content">
            <div class="analysis-section-heading">
              <div>
                <h3>阶段二 · 交易决策</h3>
                <p>包含阶段一后的策略路由、经验加载和最终决策。</p>
              </div>
              <span class="analysis-mini-status" :class="stage2Output ? 'passed' : ''">
                {{ details?.stage2_short_circuited ? "闸门短路" : stage2Output ? "已生成" : "等待中" }}
              </span>
            </div>
            <div v-if="details?.stage2_short_circuited" class="analysis-info-banner">
              阶段一 gate_result 为 wait/unknown，原版流程跳过第二次模型调用，直接生成等待结果。
            </div>
            <div v-if="strategyFiles.length" class="analysis-subsection">
              <h4>策略路由结果</h4>
              <div class="analysis-chip-list">
                <span v-for="file in strategyFiles" :key="file">{{ file }}</span>
              </div>
            </div>
            <div v-else-if="trace.some(item => item.type === 'strategy_routed')" class="analysis-subsection">
              <h4>策略路由结果</h4>
              <p class="analysis-muted">当前诊断没有匹配到额外策略文件，或路由结果为空。</p>
            </div>
            <div v-if="details?.experience_loaded_count" class="analysis-subsection">
              <h4>加载的经验案例（{{ details.experience_loaded_count }}）</h4>
              <div v-for="entry in details.experience_loaded" :key="entry.filename" class="analysis-experience-row">
                <strong>{{ entry.filename }}</strong>
                <small>{{ entry.case_type }} · {{ entry.cycle_position }}</small>
              </div>
            </div>
            <div v-else-if="details" class="analysis-subsection">
              <h4>经验案例</h4>
              <p class="analysis-muted">当前 Web 版将 experience_max_entries 设置为 0，本次没有加载历史经验案例。</p>
            </div>
            <div v-if="stage2Prompt" class="analysis-prompt-summary">
              <strong>阶段二 Prompt 构建摘要</strong>
              <div class="analysis-prompt-grid">
                <span>System {{ formatNumber(stage2Prompt.system_chars) }} 字符</span>
                <span>User {{ formatNumber(stage2Prompt.user_chars) }} 字符</span>
              </div>
              <div v-if="stage2Prompt.system_headings?.length" class="analysis-chip-list">
                <span v-for="heading in stage2Prompt.system_headings" :key="heading">{{ heading }}</span>
              </div>
            </div>
            <pre v-if="stage2Output" class="analysis-json">{{ pretty(stage2Output) }}</pre>
            <div v-else class="analysis-empty-state compact">阶段二决策尚未生成。</div>
          </section>

          <section v-else-if="activeTab === 'market'" class="analysis-tab-content">
            <div class="analysis-section-heading">
              <div>
                <h3>分析输入与市场上下文</h3>
                <p>用于本次分析的 K 线快照和衍生品上下文。</p>
              </div>
            </div>
            <div v-if="frameInfo" class="analysis-metric-grid">
              <div><small>交易对</small><strong>{{ frameInfo.symbol || job.request.symbol }}</strong></div>
              <div><small>周期</small><strong>{{ frameInfo.timeframe || job.request.timeframe }}</strong></div>
              <div><small>分析 K 线</small><strong>{{ frameInfo.bar_count ?? "--" }}</strong></div>
              <div><small>最新收盘</small><strong>{{ formatNumber(frameInfo.latest_close) }}</strong></div>
            </div>
            <template v-if="marketContext">
              <h4>衍生品上下文</h4>
              <div class="analysis-metric-grid">
                <div><small>数据来源</small><strong>{{ marketContext.exchange }}</strong></div>
                <div><small>资金费率</small><strong>{{ marketContext.funding_rate ?? "--" }}</strong></div>
                <div><small>OI（合约单位）</small><strong>{{ marketContext.open_interest ?? "--" }}</strong></div>
                <div><small>OI（USD）</small><strong>{{ marketContext.open_interest_usd ?? "--" }}</strong></div>
                <div><small>账户多空比</small><strong>{{ marketContext.global_long_short_ratio ?? "--" }}</strong></div>
                <div><small>主动交易多空比</small><strong>{{ marketContext.taker_long_short_ratio ?? "--" }}</strong></div>
              </div>
              <h4>数据质量</h4>
              <div v-if="marketContext.data_quality" class="analysis-chip-list">
                <span v-for="(value, key) in marketContext.data_quality" :key="key">{{ key }}: {{ value }}</span>
              </div>
            </template>
            <div v-else class="analysis-empty-state compact">市场数据准备完成后，会在这里显示本次输入摘要。</div>
          </section>

          <section v-else-if="activeTab === 'provenance'" class="analysis-tab-content">
            <div class="analysis-section-heading">
              <div>
                <h3>Prompt / 策略版本溯源</h3>
                <p>记录本次实际加载或路由的文件及 SHA-256，便于复现实验。</p>
              </div>
            </div>
            <template v-if="details?.provenance">
              <div class="analysis-subsection">
                <h4>Prompt 与策略文件</h4>
                <div v-for="file in details.provenance.prompt_files || []" :key="file.path" class="analysis-provenance-row">
                  <strong>{{ file.path }}</strong>
                  <code>{{ file.sha256 || "文件缺失" }}</code>
                  <small>{{ file.kind }} · {{ file.size_bytes ?? "--" }} bytes</small>
                </div>
              </div>
              <div class="analysis-subsection">
                <h4>PA 核心引擎文件</h4>
                <div v-for="file in details.provenance.engine_files || []" :key="file.path" class="analysis-provenance-row">
                  <strong>{{ file.path }}</strong>
                  <code>{{ file.sha256 || "文件缺失" }}</code>
                  <small>{{ file.size_bytes ?? "--" }} bytes</small>
                </div>
              </div>
              <div class="analysis-chip-list">
                <span>Feature {{ details.provenance.feature_schema_version }}</span>
                <span>Label {{ details.provenance.label_schema_version }}</span>
                <span>Model {{ details.provenance.model }}</span>
              </div>
            </template>
            <div v-else class="analysis-empty-state compact">当前分析尚未记录 Prompt 文件版本。</div>
          </section>

          <section v-else class="analysis-tab-content">
            <div class="analysis-section-heading">
              <div>
                <h3>安全调试记录</h3>
                <p>只包含流程元数据和校验诊断，不包含 API Key、请求头或模型隐藏推理内容。</p>
              </div>
            </div>
            <pre class="analysis-json">{{ pretty(trace) }}</pre>
          </section>
        </template>
      </div>
    </aside>
  </div>
</template>
