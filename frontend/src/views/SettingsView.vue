<script setup>
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { getJson, mutateJson } from '../api'
import Sci99Settings from '../components/Sci99Settings.vue'
import { useMarket } from '../store'
const sources = ref([]), telegram = ref(null), busy = ref(false), error = ref(''), notice = ref('')
const progressEnabled = ref(true), runs = ref([]), loaded = ref(false)
let loadSequence = 0
async function load(syncProgress = true) { const id = ++loadSequence; const [s, t, f, r] = await Promise.all([getJson('/api/sources/status'), getJson('/api/settings/telegram'), getJson('/api/admin/fetch-settings'), getJson('/api/admin/fetch-runs')]); if (id !== loadSequence) return; sources.value = s.items; useMarket().sources.value = s.items; telegram.value = t; if (syncProgress || !loaded.value) progressEnabled.value = f.progress_enabled; runs.value = r.items; loaded.value = true }
async function reloadSources() { try { await load(false) } catch (e) { error.value = e.message } }
async function saveProgress() { if (!loaded.value || busy.value) return; busy.value = true; error.value = ''; try { await mutateJson('/api/admin/fetch-settings', 'PUT', { progress_enabled: progressEnabled.value }); notice.value = '抓取进度推送设置已保存。' } catch (e) { error.value = e.message } finally { busy.value = false } }
async function fetchPrices() { busy.value = true; error.value = ''; notice.value = ''; try { const result = await mutateJson('/api/fetch/run'); notice.value = result.status === 'failed' ? '本轮抓取整体异常，请核对运行记录。' : '抓取已完成，请查看各来源状态。'; await load(false) } catch (e) { error.value = e.message } finally { busy.value = false } }
onMounted(async () => { try { await load() } catch (e) { error.value = e.message } })
onBeforeUnmount(() => { loadSequence++ })
</script>
<template>
  <h1 class="page-title">全局设置</h1>
  <p v-if="error" role="alert" class="notice error-notice">{{ error }}</p><p v-if="notice" role="status" class="notice">{{ notice }}</p>
  <Sci99Settings @saved="reloadSources" />
  <section class="panel alert-form-panel"><h2>抓取调度与通知</h2><p>北京时间每天08:00、08:30、09:00、15:30各抓一轮；09:00轮结束后生成每日行情日报。</p><form @submit.prevent="saveProgress"><label><input v-model="progressEnabled" type="checkbox" :disabled="busy || !loaded" /> 抓取进度推送（仅管理员 Telegram）</label> <button class="button" :disabled="busy || !loaded">保存进度设置</button></form></section>
  <section class="panel alert-form-panel"><h2>Telegram</h2><p>{{ telegram?.message }}</p><p>由站长在服务器 .env 配置，页面不显示凭据。<RouterLink to="/push-logs">查看我的日志 / 测试 ↗</RouterLink></p></section>
  <section class="panel alert-form-panel"><div class="panel-toolbar"><h2>数据源</h2><button class="button" :disabled="busy" @click="fetchPrices">{{ busy ? '抓取中…' : '立即抓取行情' }}</button></div><p v-for="s in sources" :key="s.source"><strong>{{ s.name }}</strong> · {{ s.message }}</p></section>
  <section class="panel alert-form-panel"><h2>最近抓取记录</h2><p v-if="!runs.length">暂无抓取记录。</p><div v-for="run in runs" :key="run.id"><h3>运行 #{{ run.id }} · {{ run.run_day }} {{ run.slot }} · {{ run.status }}</h3><p v-for="(line, index) in run.summary.items" :key="index">{{ line }}</p></div></section>
</template>
