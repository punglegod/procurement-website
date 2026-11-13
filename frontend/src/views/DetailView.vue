<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useMarket } from '../store'
import { direction, getJson, quotePrice, signed, sourceUrl, statusText, time } from '../api'
import PriceChart from '../components/PriceChart.vue'
import QuoteSelector from '../components/QuoteSelector.vue'

const route = useRoute()
const { items, loading: marketLoading, refresh: refreshMarket, refreshedAt } = useMarket()
const item = computed(() => items.value.find(row => row.code === route.params.code))
const point = computed(() => item.value?.price_point)
const days = ref(30)
const points = ref([])
const loading = ref(false)
const error = ref('')
const quoteSelector = ref(null)
let controller
let requestId = 0

async function loadHistory() {
  const id = ++requestId
  controller?.abort()
  controller = new AbortController()
  const signal = controller.signal
  loading.value = true
  error.value = ''
  points.value = []
  const timer = setTimeout(() => controller?.signal === signal && controller.abort(), 15000)
  try {
    const result = await getJson(`/api/prices/history?code=${encodeURIComponent(route.params.code)}&days=${days.value}`, signal)
    if (id === requestId) points.value = result.items
  } catch (cause) {
    if (id === requestId) error.value = cause.name === 'AbortError' ? '读取超时，请重试。' : cause.message
  } finally {
    clearTimeout(timer)
    if (id === requestId) loading.value = false
  }
}

async function refresh() { await Promise.all([refreshMarket(), loadHistory(), quoteSelector.value?.reload()]) }
async function selectionSaved() {
  requestId++; controller?.abort(); points.value = []; loading.value = true
  const code = route.params.code
  await refreshMarket({ force: true })
  if (route.params.code === code) await loadHistory()
}
watch(() => [route.params.code, days.value], loadHistory, { immediate: true })
onBeforeUnmount(() => { requestId++; controller?.abort() })
</script>

<template>
  <RouterLink to="/" class="back-link">← 返回行情总览</RouterLink>
  <template v-if="item">
    <h1 class="page-title">{{ item.name }} <span class="status-badge" :class="point ? (item.stale ? 'badge-amber' : 'badge-green') : 'badge-muted'">{{ statusText(item) }}</span></h1>
    <div v-if="point && item.stale" class="notice stale-notice" role="status">数据过期：最新报价日期为 {{ point.quote_date }}，早于最近工作日。以下保留最后真实报价。</div>
    <div v-if="item.selection_missing" class="notice stale-notice" role="status">所选口径本轮未更新，保留最后报价，不自动切换。</div>
    <div v-if="point?.unit_pending" class="notice stale-notice" role="status">所选口径单位待核定，暂不计算涨跌或发送价格提醒。</div>
    <QuoteSelector v-if="item.selected_entry_id !== undefined" ref="quoteSelector" :code="item.code" :selected-id="item.selected_entry_id" @saved="selectionSaved" />
    <section class="detail-summary" aria-label="品种最新行情">
      <div class="latest-block"><span class="metric-label">最新现货价</span><div class="latest-value">{{ quotePrice(point) }}<small>{{ point?.unit || item.unit }}</small></div><small v-if="point?.low != null">区间原文；走势与涨跌使用区间中值。</small></div>
      <div><span class="metric-label">涨跌额 <small>较上一报价日</small></span><strong class="detail-metric" :class="direction(item.change_amount)">{{ signed(item.change_amount) }}<small v-if="item.change_amount != null"> {{ item.unit }}</small></strong></div>
      <div><span class="metric-label">涨跌幅</span><strong class="detail-metric" :class="direction(item.change_percent)">{{ signed(item.change_percent, '%') }}</strong></div>
      <div><span class="metric-label">报价日期</span><strong class="detail-date">{{ point?.quote_date || '—' }}</strong><small class="muted">{{ item.previous_quote_date ? `对比 ${item.previous_quote_date}` : '暂无上一报价日数据' }}</small></div>
    </section>
    <section class="chart-panel">
      <div class="panel-toolbar chart-toolbar"><div><h2>价格走势</h2><p class="panel-subtitle">同一来源 · 同一口径 · {{ item.unit }}</p></div><div class="toolbar-actions"><button class="button" :disabled="loading || marketLoading" @click="refresh">刷新数据</button><div class="range-tabs" aria-label="走势图时间范围"><button v-for="range in [7, 30, 90]" :key="range" :class="{ selected: days === range }" :aria-pressed="days === range" @click="days = range">{{ range }} 天</button></div></div></div>
      <div v-if="loading" class="chart-placeholder" role="status">正在读取历史报价…</div>
      <div v-else-if="error" class="chart-placeholder" role="alert"><p>{{ error }}</p><button class="button" @click="loadHistory">重试</button></div>
      <template v-else-if="points.length"><PriceChart :points="points" :unit="item.unit" /><div class="chart-caption"><span>最近 {{ days }} 个自然日 · {{ points.length }} 条真实报价</span><span>{{ points.length === 1 ? '仅有 1 个报价点，暂未形成连续走势' : '按已有报价日绘制，不补造缺失报价' }}</span></div></template>
      <div v-else class="chart-placeholder"><span class="empty-chart-icon" aria-hidden="true">⌁</span><h3>这段时间暂无报价</h3><p>可切换更长时间范围；未入库的日期不补造数据。</p></div>
    </section>
    <section class="provenance-panel"><div class="panel-title"><h2>报价说明</h2></div><dl class="provenance-grid"><div><dt>当前报价口径</dt><dd>{{ point?.quote_basis || item.quote_basis }}</dd></div><div><dt>数据来源</dt><dd><a v-if="point && sourceUrl(point.source_url)" :href="sourceUrl(point.source_url)" target="_blank" rel="noopener noreferrer">{{ point.source_name }} ↗</a><span v-else>暂无入库报价；请查看数据状态</span></dd></div><div><dt>抓取时间（北京时间）</dt><dd>{{ point ? `${point.fetched_at.slice(0, 10)} ${time(point.fetched_at).split(' ').at(-1)}` : '—' }}</dd></div><div><dt>数据状态</dt><dd>{{ item.message }}</dd></div></dl><p class="provenance-note">涨跌额 = 最新价 − 上一报价日价格；涨跌幅 = 涨跌额 ÷ 上一报价日价格 × 100%。缺少对比报价时显示“—”。</p></section>
  </template>
  <div v-else class="chart-placeholder"><h1>{{ marketLoading || !refreshedAt ? '正在读取品种…' : '未找到该品种' }}</h1><RouterLink to="/" class="text-button">返回行情总览</RouterLink></div>
</template>
