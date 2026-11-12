<script setup>
import { onMounted, ref } from 'vue'
import { getJson, mutateJson, pushStatus, time } from '../api'

const channel = ref(null), logs = ref([]), total = ref(0), page = ref(1), busy = ref(false), error = ref(''), notice = ref('')
async function load() {
  const [status, data] = await Promise.all([getJson('/api/alerts/status'), getJson(`/api/alerts/logs?page=${page.value}`)])
  channel.value = status; logs.value = data.items; total.value = data.total
}
async function refresh() { busy.value = true; error.value = ''; try { await load() } catch (e) { error.value = e.message } finally { busy.value = false } }
async function testPush() {
  busy.value = true; error.value = ''; notice.value = ''
  try { const result = await mutateJson('/api/alerts/test'); notice.value = (result.results || [result]).map(r => `${r.channel || ''} ${pushStatus(r.status)}：${r.detail}`).join('；'); page.value = 1; await load() }
  catch (e) { error.value = e.message } finally { busy.value = false }
}
async function turn(delta) { page.value += delta; await refresh() }
onMounted(refresh)
</script>

<template>
  <section class="page-heading"><div><div class="eyebrow">MY DELIVERIES</div><h1>推送日志</h1><p>记录每次发送结果，保留规则删除前的提醒内容。</p></div><div class="heading-actions"><button class="button" :disabled="busy" @click="testPush">{{ busy ? '处理中…' : '发送测试推送' }}</button><button class="text-button" :disabled="busy" @click="refresh">刷新日志</button></div></section>
  <div v-if="error" class="notice error-notice" role="alert">{{ error }}</div>
  <div v-if="notice" class="notice" role="status">{{ notice }}</div>
  <section class="panel alert-form-panel"><h2>我的推送通道</h2><p>{{ channel?.message || '正在读取通道状态…' }}</p><p class="alert-help">在“我的推送设置”填写邮箱。服务未配置时显示“待配置”；管理员保留 Telegram。“发送成功”表示通道已接受，请核对实际收信。</p><RouterLink to="/push-settings">我的推送设置 ↗</RouterLink></section>
  <section class="panel"><div class="panel-toolbar"><h2>发送记录 <small>共 {{ total }} 条</small></h2><RouterLink to="/alerts" class="text-button">管理提醒规则 ↗</RouterLink></div>
    <div class="alert-table-wrap"><table class="alert-table log-table"><thead><tr><th>时间 / 类型</th><th>结果</th><th>发送内容</th></tr></thead><tbody><tr v-for="log in logs" :key="log.id"><td>{{ time(log.created_at) }}<small>{{ log.kind === 'test' ? '通道测试' : `规则 #${log.rule_id} · ${log.code}` }}</small><small>日志 #{{ log.id }} · {{ log.channel === 'email' ? '邮件' : 'Telegram' }}</small><small v-if="log.recipient">{{ log.recipient }}</small></td><td><span :class="['push-result', log.status]">{{ pushStatus(log.status) }}</span><small>{{ log.detail }}</small><small v-if="log.telegram_message_id != null">message_id: {{ log.telegram_message_id }}</small></td><td class="push-content">{{ log.message }}</td></tr></tbody></table></div>
    <p v-if="!logs.length" class="alert-empty">{{ busy ? '正在读取日志…' : '暂无发送记录，可点击“发送测试推送”检查通道。' }}</p>
    <div class="table-footer"><span>第 {{ page }} 页 · 每页 30 条</span><div><button class="text-button" :disabled="busy || page <= 1" @click="turn(-1)">上一页</button><button class="text-button" :disabled="busy || page * 30 >= total" @click="turn(1)">下一页</button></div></div>
  </section>
</template>
