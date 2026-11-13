<script setup>
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { getJson, mutateJson, time } from '../api'

const state = ref(null), enabled = ref(false), username = ref(''), password = ref(''), cookie = ref('')
const emit = defineEmits(['saved'])
const busy = ref(false), error = ref(''), notice = ref('')
let sequence = 0
async function load() {
  const id = ++sequence; busy.value = true; error.value = ''
  try { const data = await getJson('/api/admin/sources/sci99'); if (id === sequence) { state.value = data; enabled.value = data.enabled } }
  catch (e) { if (id === sequence) error.value = e.message }
  finally { if (id === sequence) busy.value = false }
}
async function operate(action) {
  if (busy.value || !state.value) return
  const id = ++sequence; busy.value = true; error.value = ''; notice.value = ''
  try {
    if (action === 'test') {
      const data = await mutateJson('/api/admin/sources/sci99/test')
      if (id === sequence) notice.value = data.message
    } else {
      const body = action === 'clear' ? { enabled: false, clear_credentials: true } : { enabled: enabled.value }
      if (action !== 'clear') for (const [key, value] of Object.entries({ username: username.value, password: password.value, cookie: cookie.value })) if (value) body[key] = value
      const data = await mutateJson('/api/admin/sources/sci99', 'PUT', body)
      if (id === sequence) { state.value = data; enabled.value = data.enabled; notice.value = action === 'clear' ? '后台凭据已清除，已关闭启用；凭据读取回落服务器配置。' : '配置已保存。' }
    }
    if (action === 'test' && id === sequence) { const data = await getJson('/api/admin/sources/sci99'); if (id === sequence) state.value = data }
    if (id === sequence) emit('saved')
  } catch (e) { if (id === sequence) error.value = e.message }
  finally {
    // 无论成功或失败都清除敏感输入，不将服务器掩码当作凭据提交。
    username.value = password.value = cookie.value = ''
    if (id === sequence) busy.value = false
  }
}
onMounted(load)
onBeforeUnmount(() => { sequence++ })
</script>
<template>
  <section class="panel alert-form-panel" aria-label="数据源配置">
    <h2>数据源配置 · 卓创资讯</h2>
    <p v-if="state">{{ state.message }}</p>
    <p v-if="state && !state.key_ready" class="notice">服务器未配置有效 SETTINGS_ENCRYPTION_KEY，不能保存凭据。请先由运维配置密钥。</p>
    <form class="account-form" @submit.prevent="operate('save')">
      <label class="checkbox-label"><input v-model="enabled" type="checkbox" :disabled="busy || !state" /> 启用卓创（自动16:30，手动不限次）</label>
      <label>账号 <span v-if="state?.username_mask">当前：{{ state.username_mask }}</span><input v-model="username" autocomplete="off" maxlength="254" :disabled="busy || !state?.key_ready" placeholder="留空保留当前配置" /></label>
      <label>密码 <span v-if="state?.password_mask">当前：{{ state.password_mask }}</span><input v-model="password" type="password" autocomplete="new-password" maxlength="254" :disabled="busy || !state?.key_ready" placeholder="留空保留当前配置" /></label>
      <label>Cookie（人工官网登录后配置）<span v-if="state?.cookie_mask">当前：{{ state.cookie_mask }}</span><textarea v-model="cookie" autocomplete="off" maxlength="8192" rows="3" :disabled="busy || !state?.key_ready" placeholder="官网账密登录依赖 JS；留空保留已保存 Cookie" /></label>
      <div class="toolbar-actions"><button class="button" :disabled="busy || !state">保存</button><button type="button" class="button" :disabled="busy || !state || !!username || !!password || !!cookie" @click="operate('test')">测试连接</button></div>
      <p v-if="username || password || cookie">请先保存凭据，再测试已保存配置。</p>
      <button v-if="state?.backend_configured" type="button" class="text-button" :disabled="busy" @click="operate('clear')">清除后台凭据并回退服务器配置</button>
    </form>
    <p v-if="state">上次登录或抓取：{{ state.last_result }} <span v-if="state.last_attempt_at">（{{ time(state.last_attempt_at) }}）</span></p>
    <p v-if="state?.auth_message" data-testid="sci99-auth-state">登录态：{{ state.auth_message }}<span v-if="state.verified_at">（验证时间：{{ time(state.verified_at) }}）</span><span v-else-if="state.auth_checked_at">（检查时间：{{ time(state.auth_checked_at) }}）</span></p>
    <p v-if="state?.quote_message" data-testid="sci99-quote-state">报价：{{ state.quote_message }}</p>
    <p>后台凭据加密保存，优先于服务器配置；测试连接只验证登录。开启后10品类常规采集改走卓创，每天16:30自动一次，失败当轮免费兜底；手动可选品类且不限次。A00仍走免费源。每个账号在详情自选口径，验证码或风控立即停止。</p>
    <p v-if="error" role="alert" class="notice error-notice">{{ error }}<button v-if="!state" class="text-button" @click="load">重新读取</button></p>
    <p v-if="notice" role="status" class="notice">{{ notice }}</p>
  </section>
</template>
