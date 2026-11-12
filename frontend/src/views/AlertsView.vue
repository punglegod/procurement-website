<script setup>
import { onMounted, reactive, ref } from 'vue'
import { getJson, mutateJson, pushStatus } from '../api'

const rules = ref([]), commodities = ref([]), channel = ref(null), error = ref(''), notice = ref(''), busy = ref(false), editing = ref(null)
const form = reactive({ code: 'alu_a00', kind: 'daily_change', threshold: 1, enabled: true })
const kinds = { daily_change: '日涨跌幅绝对值 ≥', price_gte: '价格 ≥', price_lte: '价格 ≤' }
const number = value => new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 4 }).format(value)
async function load() {
  const [data, catalog] = await Promise.all([getJson('/api/alerts/rules'), getJson('/api/commodities')])
  rules.value = data.items; channel.value = data.channel; commodities.value = catalog.items
}
async function action(callback) {
  busy.value = true; error.value = ''; notice.value = ''
  try { await callback(); await load() } catch (e) { error.value = e.message } finally { busy.value = false }
}
function reset() { editing.value = null; Object.assign(form, { code: 'alu_a00', kind: 'daily_change', threshold: 1, enabled: true }) }
function edit(rule) { editing.value = rule.id; Object.assign(form, { code: rule.code, kind: rule.kind, threshold: rule.threshold, enabled: rule.enabled }); window.scrollTo({ top: 0, behavior: 'smooth' }) }
function save() { action(async () => { await mutateJson(`/api/alerts/rules${editing.value ? '/' + editing.value : ''}`, editing.value ? 'PUT' : 'POST', { ...form }); notice.value = '规则已保存，将在下次检查时生效。'; reset() }) }
function remove(rule) { action(async () => { await mutateJson(`/api/alerts/rules/${rule.id}`, 'DELETE'); if (editing.value === rule.id) reset(); notice.value = `规则 #${rule.id} 已删除，推送日志仍保留。` }) }
function toggle(rule) { action(async () => { await mutateJson(`/api/alerts/rules/${rule.id}`, 'PUT', { code: rule.code, kind: rule.kind, threshold: rule.threshold, enabled: !rule.enabled }) }) }
function check() { action(async () => { const data = await mutateJson('/api/alerts/check'); notice.value = data.results.length ? data.results.map(r => `#${r.rule_id} ${pushStatus(r.status)}：${r.detail}`).join('；') : '暂无启用的规则。' }) }
onMounted(() => action(async () => {}))
</script>

<template>
  <section class="page-heading"><div><div class="eyebrow">PRICE ALERTS</div><h1>提醒规则</h1><p>按固定现货口径提醒，及时掌握价格变化。</p></div><button class="button" :disabled="busy" @click="check">立即检查规则</button></section>
  <div v-if="error" role="alert" class="notice error-notice">{{ error }}<button class="text-button" :disabled="busy" @click="action(async () => {})">重新读取</button></div>
  <div v-if="notice" role="status" class="notice">{{ notice }}</div>
  <div class="notice" v-if="channel">Telegram：{{ channel.message }} <RouterLink to="/push-logs" class="text-button">查看日志 / 测试推送 ↗</RouterLink></div>
  <section class="panel alert-form-panel">
    <h2>{{ editing ? `编辑规则 #${editing}` : '新增提醒' }}</h2>
    <form class="alert-form" @submit.prevent="save">
      <label>品种<select v-model="form.code" :disabled="busy"><option v-for="item in commodities" :key="item.code" :value="item.code">{{ item.name }}</option></select></label>
      <label>提醒条件<select v-model="form.kind" :disabled="busy"><option v-for="(label, key) in kinds" :key="key" :value="key">{{ label }}</option></select></label>
      <label>阈值（{{ form.kind === 'daily_change' ? '%' : commodities.find(c => c.code === form.code)?.unit || '元/吨' }}）<input v-model.number="form.threshold" :disabled="busy" type="number" min="0.0001" max="9999999999.9999" step="0.0001" required /></label>
      <label class="checkbox-label"><input type="checkbox" v-model="form.enabled" :disabled="busy" /> 启用</label>
      <button class="button" type="submit" :disabled="busy">{{ busy ? '处理中…' : '保存规则' }}</button>
      <button v-if="editing" class="text-button" type="button" :disabled="busy" @click="reset">取消编辑</button>
    </form>
    <p class="alert-help">日涨跌幅取绝对值，含上涨和下跌，比较上一真实报价日；按未四舍五入的数值判断。每条规则独立触发，同规则同品种每天最多尝试发送一次（北京时间）。</p>
  </section>
  <section class="panel">
    <div class="panel-toolbar"><h2>已保存规则 <small>{{ rules.length }} 条</small></h2></div>
    <div class="alert-table-wrap"><table class="alert-table"><thead><tr><th>品种 / 固定口径</th><th>条件</th><th>状态 / 当前检查</th><th>操作</th></tr></thead><tbody>
      <tr v-for="rule in rules" :key="rule.id"><td><strong>{{ rule.name }}</strong><small>#{{ rule.id }} · {{ rule.quote_basis }}</small><small>{{ rule.unit }}</small></td><td>{{ kinds[rule.kind] }} {{ number(rule.threshold) }}{{ rule.kind === 'daily_change' ? '%' : ' ' + rule.unit }}</td><td>{{ rule.enabled ? '已启用' : '已暂停' }}<small>{{ rule.data_message }}</small></td><td class="alert-actions"><button class="text-button" :disabled="busy" @click="edit(rule)">编辑</button><button class="text-button" :disabled="busy" @click="toggle(rule)">{{ rule.enabled ? '暂停' : '启用' }}</button><button class="text-button" :disabled="busy" @click="remove(rule)">删除</button></td></tr>
    </tbody></table></div>
    <p v-if="!rules.length" class="alert-empty">{{ busy ? '正在读取规则…' : '暂无规则，从上方添加第一条价格提醒。' }}</p>
  </section>
  <p class="alert-help">仅当天报价参与推送；旧报价、无报价或缺少涨跌对比均跳过。入库后及每 5 分钟检查一次，服务需保持运行。发送失败、超时或中断也保留当日额度，避免重复消息。</p>
</template>
