<script setup>
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { getJson, mutateJson } from '../api'
import Sci99Settings from '../components/Sci99Settings.vue'
import { useMarket } from '../store'
const sources = ref([]), telegram = ref(null), busy = ref(false), error = ref(''), notice = ref('')
const progressEnabled = ref(true), runs = ref([]), loaded = ref(false)
const commodities = ref([]), selectedCodes = ref([]), lastResults = ref([])
let catalogLoaded = false
let loadSequence = 0
async function load(syncProgress = true) { const id = ++loadSequence; const [s, t, f, r, c] = await Promise.all([getJson('/api/sources/status'), getJson('/api/settings/telegram'), getJson('/api/admin/fetch-settings'), getJson('/api/admin/fetch-runs'), getJson('/api/commodities')]); if (id !== loadSequence) return; sources.value = s.items; useMarket().sources.value = s.items; telegram.value = t; if (syncProgress || !loaded.value) progressEnabled.value = f.progress_enabled; runs.value = r.items; commodities.value = c.items || []; if (!catalogLoaded) { selectedCodes.value = commodities.value.map(item => item.code); catalogLoaded = true } loaded.value = true }
async function reloadSources() { try { await load(false) } catch (e) { error.value = e.message } }
async function saveProgress() { if (!loaded.value || busy.value) return; busy.value = true; error.value = ''; try { await mutateJson('/api/admin/fetch-settings', 'PUT', { progress_enabled: progressEnabled.value }); notice.value = '抓取进度推送设置已保存。' } catch (e) { error.value = e.message } finally { busy.value = false } }
async function fetchPrices() { if (busy.value || !loaded.value || !selectedCodes.value.length) return; busy.value = true; error.value = ''; notice.value = ''; lastResults.value = []; try { const result = await mutateJson('/api/fetch/run', 'POST', { codes: [...selectedCodes.value] }); lastResults.value = result.commodity_results || []; notice.value = result.status === 'failed' ? '本轮抓取整体异常，请核对运行记录。' : result.status === 'partial' ? '本轮部分来源异常，免费兜底结果见下方。' : '抓取已完成，请查看逐品类结果。'; await load(false) } catch (e) { error.value = e.message } finally { busy.value = false } }
onMounted(async () => { try { await load() } catch (e) { error.value = e.message } })
onBeforeUnmount(() => { loadSequence++ })
</script>
<template>
  <h1 class="page-title">全局设置</h1>
  <p v-if="error" role="alert" class="notice error-notice">{{ error }}</p><p v-if="notice" role="status" class="notice">{{ notice }}</p>
  <Sci99Settings @saved="reloadSources" />
  <section class="panel alert-form-panel"><h2>抓取调度与通知</h2><p>北京时间08:00、08:30、09:00、15:30处理免费源待办；已抓到今日有效价格的品类跳过。卓创自动轮为16:30，失败当轮免费兜底；09:00轮结束后仍发送每日行情日报，卓创使用最近已结束报价日的收盘价。</p><form @submit.prevent="saveProgress"><label><input v-model="progressEnabled" type="checkbox" :disabled="busy || !loaded" /> 抓取进度推送（仅管理员 Telegram）</label> <button class="button" :disabled="busy || !loaded">保存进度设置</button></form></section>
  <section class="panel alert-form-panel"><h2>Telegram</h2><p>{{ telegram?.message }}</p><p>由站长在服务器 .env 配置，页面不显示凭据。<RouterLink to="/push-logs">查看我的日志 / 测试 ↗</RouterLink></p></section>
  <section class="panel alert-form-panel"><div class="panel-toolbar"><h2>手动抓取</h2><button class="button" :disabled="busy || !loaded || !selectedCodes.length" @click="fetchPrices">{{ busy ? '抓取中…' : '立即抓取行情' }}</button></div><fieldset :disabled="busy || !loaded"><legend>选择品类</legend><label v-for="item in commodities" :key="item.code" class="checkbox-label"><input v-model="selectedCodes" type="checkbox" :value="item.code" /> {{ item.name }} </label></fieldset><p>只抓勾选品类。卓创开启时优先卓创，失败免费兜底；手动不限次，不占自动额度，也不受“今日已抓取”限制。验证码或登录异常仍立即停手。</p><p v-for="result in lastResults" :key="result.code">{{ commodities.find(item => item.code === result.code)?.name || result.code }} · {{ { success: '成功', failed: '失败', skipped: '跳过' }[result.outcome] }} · {{ result.message }}</p></section>
  <section class="panel alert-form-panel"><h2>数据源</h2><p v-for="s in sources" :key="s.source"><strong>{{ s.name }}</strong> · {{ s.message }}</p></section>
  <section class="panel alert-form-panel"><h2>最近抓取记录</h2><p v-if="!runs.length">暂无抓取记录。</p><div v-for="run in runs" :key="run.id"><h3>运行 #{{ run.id }} · {{ run.run_day }} {{ run.slot }} · {{ run.status }}</h3><p v-for="(line, index) in run.summary.items" :key="index">{{ line }}</p></div></section>
</template>
