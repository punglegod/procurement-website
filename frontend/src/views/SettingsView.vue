<script setup>
import { onMounted, ref } from 'vue'
import { getJson, mutateJson } from '../api'
const sources = ref([]), telegram = ref(null), busy = ref(false), error = ref(''), notice = ref('')
async function load() { const [s, t] = await Promise.all([getJson('/api/sources/status'), getJson('/api/settings/telegram')]); sources.value = s.items; telegram.value = t }
async function fetchPrices() { busy.value = true; error.value = ''; notice.value = ''; try { await mutateJson('/api/fetch/run'); notice.value = '抓取已完成，请查看各来源状态。'; await load() } catch (e) { error.value = e.message } finally { busy.value = false } }
onMounted(async () => { try { await load() } catch (e) { error.value = e.message } })
</script>
<template>
  <h1 class="page-title">全局设置</h1>
  <p v-if="error" role="alert" class="notice error-notice">{{ error }}</p><p v-if="notice" role="status" class="notice">{{ notice }}</p>
  <section class="panel alert-form-panel"><h2>Telegram</h2><p>{{ telegram?.message }}</p><p>由站长在服务器 .env 配置，页面不显示凭据。<RouterLink to="/push-logs">查看我的日志 / 测试 ↗</RouterLink></p></section>
  <section class="panel alert-form-panel"><div class="panel-toolbar"><h2>数据源</h2><button class="button" :disabled="busy" @click="fetchPrices">{{ busy ? '抓取中…' : '立即抓取行情' }}</button></div><p v-for="s in sources" :key="s.source"><strong>{{ s.name }}</strong> · {{ s.message }}</p></section>
</template>
