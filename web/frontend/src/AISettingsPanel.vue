<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import {
  api,
  type AIProviderInput,
  type AIProviderList,
  type AIProviderPublic,
  type AIProviderTestResult,
} from "./api";

const emit = defineEmits<{ close: []; saved: [] }>();
const providers = ref<AIProviderPublic[]>([]);
const selectedId = ref<string | null>(null);
const adminToken = ref(sessionStorage.getItem("pa_ai_admin_token") || "");
const authRequired = ref(false);
const loading = ref(false);
const saving = ref(false);
const testing = ref(false);
const deleting = ref(false);
const error = ref("");
const notice = ref("");
const testResult = ref<AIProviderTestResult | null>(null);
const showApiKey = ref(false);
const showBackupKey = ref(false);
const clearApiKey = ref(false);
const clearBackupKey = ref(false);

const draft = reactive({
  name: "DeepSeek",
  base_url: "https://api.deepseek.com",
  model: "deepseek-chat",
  api_key: "",
  backup_glm_api_key: "",
  thinking: true,
  reasoning_effort: "medium" as "low" | "medium" | "high" | "max",
  context_window: 200000,
  activate: true,
});

const selectedProvider = computed(
  () => providers.value.find((item) => item.id === selectedId.value) ?? null,
);
const keyPlaceholder = computed(() => {
  if (selectedProvider.value?.has_api_key) {
    return "已保存 " + selectedProvider.value.api_key_masked + "；留空表示保持原 Key";
  }
  return "输入服务商 API Key";
});
const backupKeyPlaceholder = computed(() => {
  if (selectedProvider.value?.has_backup_glm_api_key) {
    return "已保存 " + selectedProvider.value.backup_glm_api_key_masked + "；留空表示保持原 Key";
  }
  return "可选：GLM 备用 API Key";
});

function onAdminTokenInput(event: Event) {
  setToken((event.target as HTMLInputElement).value);
}

function onPresetInput(event: Event) {
  applyPreset((event.target as HTMLSelectElement).value);
}

function setToken(value: string) {
  adminToken.value = value.trim();
  if (adminToken.value) sessionStorage.setItem("pa_ai_admin_token", adminToken.value);
  else sessionStorage.removeItem("pa_ai_admin_token");
}

async function loadProviders() {
  loading.value = true;
  error.value = "";
  try {
    const result: AIProviderList = await api.aiProviders(adminToken.value);
    providers.value = result.providers;
    authRequired.value = false;
    if (!selectedId.value && result.active_provider_id) {
      selectProvider(result.active_provider_id);
    }
  } catch (err) {
    const message = err instanceof Error ? err.message : "读取 AI 配置失败";
    if (message.includes("令牌") || message.includes("访问保护") || message.includes("管理令牌")) {
      authRequired.value = true;
      error.value = message;
    } else {
      error.value = message;
    }
  } finally {
    loading.value = false;
  }
}

function resetDraft() {
  selectedId.value = null;
  Object.assign(draft, {
    name: "DeepSeek",
    base_url: "https://api.deepseek.com",
    model: "deepseek-chat",
    api_key: "",
    backup_glm_api_key: "",
    thinking: true,
    reasoning_effort: "medium",
    context_window: 200000,
    activate: true,
  });
  clearApiKey.value = false;
  clearBackupKey.value = false;
  showApiKey.value = false;
  showBackupKey.value = false;
  testResult.value = null;
  notice.value = "";
}

function selectProvider(id: string) {
  const item = providers.value.find((provider) => provider.id === id);
  if (!item) return;
  selectedId.value = item.id;
  Object.assign(draft, {
    name: item.name,
    base_url: item.base_url,
    model: item.model,
    api_key: "",
    backup_glm_api_key: "",
    thinking: item.thinking,
    reasoning_effort: item.reasoning_effort,
    context_window: item.context_window,
    activate: item.active,
  });
  clearApiKey.value = false;
  clearBackupKey.value = false;
  showApiKey.value = false;
  showBackupKey.value = false;
  testResult.value = null;
  notice.value = "";
}

function applyPreset(preset: string) {
  if (preset === "deepseek") {
    draft.name = "DeepSeek";
    draft.base_url = "https://api.deepseek.com";
    draft.model = "deepseek-chat";
  } else if (preset === "openai") {
    draft.name = "OpenAI";
    draft.base_url = "https://api.openai.com/v1";
    draft.model = "gpt-5-mini";
  } else if (preset === "custom") {
    draft.name = "自定义模型";
    draft.base_url = "";
    draft.model = "";
  }
  testResult.value = null;
}

async function testConnection() {
  error.value = "";
  notice.value = "";
  testResult.value = null;
  testing.value = true;
  try {
    testResult.value = await api.testAIProvider({
      id: selectedId.value,
      base_url: draft.base_url,
      model: draft.model,
      api_key: draft.api_key || undefined,
    }, adminToken.value);
  } catch (err) {
    error.value = err instanceof Error ? err.message : "模型连接测试失败";
  } finally {
    testing.value = false;
  }
}

async function saveProvider() {
  error.value = "";
  notice.value = "";
  saving.value = true;
  try {
    const payload: AIProviderInput = {
      id: selectedId.value,
      name: draft.name,
      base_url: draft.base_url,
      model: draft.model,
      api_key: draft.api_key || undefined,
      clear_api_key: clearApiKey.value,
      backup_glm_api_key: draft.backup_glm_api_key || undefined,
      clear_backup_glm_api_key: clearBackupKey.value,
      thinking: draft.thinking,
      reasoning_effort: draft.reasoning_effort,
      context_window: Number(draft.context_window),
      activate: draft.activate,
    };
    const result = await api.saveAIProvider(payload, adminToken.value);
    providers.value = result.providers;
    selectedId.value = result.saved_provider_id ?? result.active_provider_id;
    draft.api_key = "";
    draft.backup_glm_api_key = "";
    clearApiKey.value = false;
    clearBackupKey.value = false;
    testResult.value = null;
    notice.value = "配置已加密保存。" + (result.active_provider_id === selectedId.value ? " 当前模型已启用。" : "");
    emit("saved");
    await loadProviders();
  } catch (err) {
    error.value = err instanceof Error ? err.message : "保存 AI 配置失败";
  } finally {
    saving.value = false;
  }
}

async function activateProvider(id: string) {
  error.value = "";
  notice.value = "";
  try {
    const result = await api.activateAIProvider(id, adminToken.value);
    providers.value = result.providers;
    selectProvider(id);
    notice.value = "当前 AI 模型已切换。";
    emit("saved");
  } catch (err) {
    error.value = err instanceof Error ? err.message : "切换模型失败";
  }
}

async function deleteProvider() {
  if (!selectedId.value || !window.confirm("确定删除这个 AI 模型配置吗？")) return;
  deleting.value = true;
  error.value = "";
  try {
    const result = await api.deleteAIProvider(selectedId.value, adminToken.value);
    providers.value = result.providers;
    resetDraft();
    notice.value = "模型配置已删除。";
    emit("saved");
  } catch (err) {
    error.value = err instanceof Error ? err.message : "删除模型配置失败";
  } finally {
    deleting.value = false;
  }
}

onMounted(() => void loadProviders());
</script>

<template>
  <div class="ai-settings-backdrop" @click.self="emit('close')">
    <section class="ai-settings-modal panel" role="dialog" aria-modal="true" aria-label="AI 模型设置">
      <header class="ai-settings-header">
        <div>
          <div class="eyebrow">MODEL PROVIDER</div>
          <h2>AI 模型设置</h2>
          <p>配置兼容 OpenAI Chat Completions 的模型服务，保存后用于 PA 两阶段分析。</p>
        </div>
        <button class="icon-close-button" aria-label="关闭" @click="emit('close')">×</button>
      </header>

      <div v-if="authRequired" class="ai-admin-auth">
        <h3>管理员验证</h3>
        <div v-if="error" class="ai-inline-error">{{ error }}</div>
        <p>本地开发环境通常无需令牌。服务器部署时，请输入管理员设置令牌。</p>
        <label>
          <span>AI_SETTINGS_ADMIN_TOKEN</span>
          <input
            :value="adminToken"
            type="password"
            autocomplete="current-password"
            placeholder="输入服务器管理员令牌"
            @input="onAdminTokenInput"
            @keyup.enter="loadProviders"
          />
        </label>
        <button class="primary-button" :disabled="loading" @click="loadProviders">
          {{ loading ? "验证中…" : "验证并读取配置" }}
        </button>
      </div>

      <template v-else>
        <div class="ai-settings-layout">
          <aside class="ai-provider-list">
            <div class="ai-list-heading">
              <strong>已保存的模型</strong>
              <button class="secondary-button" @click="resetDraft">＋ 新增</button>
            </div>
            <button
              v-for="provider in providers"
              :key="provider.id"
              class="ai-provider-item"
              :class="{ selected: selectedId === provider.id }"
              @click="selectProvider(provider.id)"
            >
              <span class="ai-provider-item-title">
                <strong>{{ provider.name }}</strong>
                <span v-if="provider.active" class="data-badge">当前使用</span>
              </span>
              <small>{{ provider.model }}</small>
              <small>{{ provider.has_api_key ? "Key 已配置 " + provider.api_key_masked : "尚未配置 API Key" }}</small>
            </button>
            <div v-if="!providers.length" class="ai-empty-list">
              还没有模型配置。点击「新增」开始配置。
            </div>
          </aside>

          <div class="ai-provider-form">
            <div class="ai-form-topline">
              <h3>{{ selectedId ? "编辑模型配置" : "添加模型配置" }}</h3>
              <span v-if="selectedProvider?.active" class="data-badge">ACTIVE</span>
            </div>

            <label class="ai-field">
              <span>快速选择服务商</span>
              <select @change="onPresetInput">
                <option value="">选择预设（也可以自定义）</option>
                <option value="deepseek">DeepSeek</option>
                <option value="openai">OpenAI</option>
                <option value="custom">自定义 OpenAI 兼容接口</option>
              </select>
            </label>

            <label class="ai-field">
              <span>配置名称</span>
              <input v-model="draft.name" placeholder="例如：DeepSeek 主模型" />
            </label>
            <label class="ai-field">
              <span>API Base URL</span>
              <input v-model="draft.base_url" spellcheck="false" placeholder="https://api.deepseek.com" />
            </label>
            <label class="ai-field">
              <span>模型名称（Model）</span>
              <input v-model="draft.model" spellcheck="false" placeholder="deepseek-chat" />
            </label>

            <label class="ai-field">
              <span>API Key</span>
              <div class="ai-secret-input">
                <input
                  v-model="draft.api_key"
                  :type="showApiKey ? 'text' : 'password'"
                  autocomplete="new-password"
                  :placeholder="keyPlaceholder"
                />
                <button class="secondary-button" @click="showApiKey = !showApiKey">
                  {{ showApiKey ? "隐藏" : "显示" }}
                </button>
              </div>
              <small>留空会保留已保存的 Key；新配置必须填写 Key 才能启用。</small>
            </label>

            <label class="ai-field">
              <span>GLM 备用 API Key（可选，仅 SenseNova / 火山方舟主通道启用）</span>
              <div class="ai-secret-input">
                <input
                  v-model="draft.backup_glm_api_key"
                  :type="showBackupKey ? 'text' : 'password'"
                  autocomplete="new-password"
                  :placeholder="backupKeyPlaceholder"
                />
                <button class="secondary-button" @click="showBackupKey = !showBackupKey">
                  {{ showBackupKey ? "隐藏" : "显示" }}
                </button>
              </div>
            </label>

            <div class="ai-inline-options">
              <label class="ai-check-field">
                <input v-model="draft.thinking" type="checkbox" />
                <span>启用 Thinking</span>
              </label>
              <label class="ai-field ai-effort-field">
                <span>Reasoning Effort</span>
                <select v-model="draft.reasoning_effort">
                  <option value="low">low</option>
                  <option value="medium">medium</option>
                  <option value="high">high</option>
                  <option value="max">max</option>
                </select>
              </label>
              <label class="ai-field ai-context-field">
                <span>Context Window（tokens）</span>
                <input v-model.number="draft.context_window" type="number" min="1024" max="2000000" step="1024" />
              </label>
            </div>

            <label class="ai-check-field ai-activate-check">
              <input v-model="draft.activate" type="checkbox" />
              <span>保存后设为当前使用模型</span>
            </label>

            <label v-if="selectedId && selectedProvider?.has_api_key" class="ai-check-field ai-danger-check">
              <input v-model="clearApiKey" type="checkbox" />
              <span>清除已保存的主 API Key</span>
            </label>
            <label v-if="selectedId && selectedProvider?.has_backup_glm_api_key" class="ai-check-field ai-danger-check">
              <input v-model="clearBackupKey" type="checkbox" />
              <span>清除已保存的 GLM 备用 Key</span>
            </label>

            <div v-if="testResult" class="ai-test-result" :class="testResult.ok ? 'success' : 'failure'">
              <strong>{{ testResult.ok ? "连接成功" : "连接失败" }}</strong>
              <span v-if="testResult.ok">模型：{{ testResult.model }} · 延迟：{{ testResult.latency_ms }} ms · Tokens：{{ testResult.total_tokens ?? "--" }}</span>
              <span v-if="testResult.ok && testResult.response_preview">{{ testResult.response_preview }}</span>
              <span v-if="!testResult.ok">{{ testResult.error }}</span>
            </div>
            <div v-if="error" class="ai-inline-error">{{ error }}</div>
            <div v-if="notice" class="ai-inline-notice">{{ notice }}</div>

            <div class="ai-settings-actions">
              <button class="secondary-button" :disabled="testing" @click="testConnection">
                {{ testing ? "正在测试…" : "测试模型连接" }}
              </button>
              <button class="primary-button" :disabled="saving" @click="saveProvider">
                {{ saving ? "保存中…" : "保存配置" }}
              </button>
              <button
                v-if="selectedId && !selectedProvider?.active"
                class="secondary-button"
                @click="activateProvider(selectedId)"
              >立即启用</button>
              <button
                v-if="selectedId"
                class="danger-button"
                :disabled="deleting"
                @click="deleteProvider"
              >{{ deleting ? "删除中…" : "删除" }}</button>
            </div>
          </div>
        </div>
      </template>

      <footer class="ai-settings-footer">
        <span>API Key 仅发送到本机后端，并使用 Fernet 加密保存；接口不会返回明文 Key。</span>
        <span>两阶段分析继续使用原版 PA Agent 引擎。</span>
      </footer>
    </section>
  </div>
</template>
