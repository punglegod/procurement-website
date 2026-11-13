<script setup>
import { onBeforeUnmount, ref, watch } from 'vue'
import { getJson, mutateJson, quotePrice } from '../api'

const props = defineProps({ code: { type: String, required: true }, selectedId: Number })
const emit = defineEmits(['saved'])
const options = ref([])
const selected = ref(null)
const loading = ref(false)
const saving = ref(false)
const error = ref('')
let sequence = 0
let controller
const reported = entry => entry.reported_point || entry.price_point

async function load() {
  const id = ++sequence
  controller?.abort()
  controller = new AbortController()
  const signal = controller.signal
  const timer = setTimeout(() => { if (id === sequence) controller.abort() }, 15000)
  loading.value = true; error.value = ''; options.value = []
  try {
    const result = await getJson(`/api/quotes?code=${encodeURIComponent(props.code)}`, signal)
    if (id === sequence) { options.value = result.items; selected.value = result.selected_entry_id }
  } catch (e) { if (id === sequence) error.value = e.name === 'AbortError' ? '报价条目读取已取消，请重试。' : e.message }
  finally { clearTimeout(timer); if (id === sequence) loading.value = false }
}

async function save(event) {
  const entryId = Number(event.target.value)
  const id = sequence
  saving.value = true; error.value = ''
  try {
    const result = await mutateJson(`/api/me/quote-selections/${encodeURIComponent(props.code)}`, 'PUT', { entry_id: entryId })
    if (id === sequence) {
      selected.value = result.selected_entry_id; options.value = result.items
      emit('saved')
    }
  } catch (e) { if (id === sequence) { error.value = e.message; event.target.value = String(selected.value ?? '') } }
  finally { if (id === sequence) saving.value = false }
}
watch(() => props.code, () => { saving.value = false; load() }, { immediate: true })
watch(() => props.selectedId, value => { if (!saving.value) selected.value = value })
onBeforeUnmount(() => { sequence++; controller?.abort() })
defineExpose({ reload: load })
</script>

<template>
  <section class="panel quote-options" aria-label="口径选择与全部报价">
    <div class="panel-toolbar"><h2>当前口径</h2><label>选择报价条目 <select aria-label="当前报价口径" :value="selected ?? ''" :disabled="loading || saving || !options.length" @change="save"><option v-if="!options.length" value="">{{ loading ? '读取中…' : '暂无报价条目' }}</option><option v-for="entry in options" :key="entry.id" :value="entry.id">{{ [entry.product_name, entry.specification, entry.market, entry.price_type, entry.source_name].filter(Boolean).join(' / ') }}</option></select></label></div>
    <p class="panel-subtitle">选中即保存，仅影响本账号的行情、走势、规则提醒和日报；已有规则阈值不变，换单位后请核对阈值。</p>
    <p v-if="error" role="alert" class="notice error-notice">{{ error }} <button class="text-button" :disabled="loading || saving" @click="load">重新读取</button></p>
    <h2>全部报价</h2>
    <div class="table-scroll"><table class="price-table"><thead><tr><th>商品 / 规格</th><th>市场 / 类型</th><th>价格或区间</th><th>涨跌原文</th><th>报价日期</th><th>单位</th><th>来源 / 状态</th></tr></thead><tbody><tr v-for="entry in options" :key="entry.id" :class="{ 'selected-quote-row': entry.id === selected }" :aria-selected="entry.id === selected"><td>{{ entry.product_name }}<small>{{ entry.specification || '—' }}{{ entry.id === selected ? ' · 当前口径' : '' }}</small></td><td>{{ entry.market || '未注明市场' }}<small>{{ entry.price_type }}</small></td><td>{{ quotePrice(reported(entry)) }}</td><td>{{ reported(entry)?.change_text || '—' }}</td><td>{{ reported(entry)?.quote_date || '—' }}</td><td>{{ reported(entry)?.unit || '—' }}</td><td>{{ entry.source_name }}<small v-if="entry.missing" class="text-amber">本轮未更新</small></td></tr><tr v-if="!loading && !options.length"><td colspan="7">暂无入库报价；抓取成功后可选择口径。</td></tr></tbody></table></div>
  </section>
</template>
