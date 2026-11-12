<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { getJson, mutateJson } from '../api'
const email = ref(''), savedEmail = ref(''), emailVerified = ref(false), code = ref('')
const channel = ref(null), error = ref(''), notice = ref(''), busy = ref(false), loading = ref(true), loaded = ref(false)
const dirty = computed(() => email.value !== savedEmail.value)
const canVerify = computed(() => loaded.value && !loading.value && !busy.value && !!savedEmail.value && !dirty.value && !emailVerified.value)
let requestId = 0, editVersion = 0
function applySettings(data, replaceEmail = true) {
  savedEmail.value = data.email || ''; emailVerified.value = data.email_verified === true; channel.value = data.channel
  if (replaceEmail) email.value = savedEmail.value
  code.value = ''
}
async function load() {
  const id = ++requestId, startingEdit = editVersion
  loading.value = true; error.value = ''
  try {
    const data = await getJson('/api/me/push-settings')
    // 读取晚到或用户已开始编辑时，不覆盖新输入。
    if (id !== requestId) return
    applySettings(data, startingEdit === editVersion); loaded.value = true
  } catch (e) { if (id === requestId) error.value = e.message }
  finally { if (id === requestId) loading.value = false }
}
async function save() {
  if (!loaded.value || loading.value || busy.value) return
  const id = ++requestId, submittedEmail = email.value.trim()
  busy.value = true; error.value = ''; notice.value = ''
  try {
    const data = await mutateJson('/api/me/push-settings', 'PUT', { email: submittedEmail })
    if (id !== requestId) return
    // 成功后固定使用本次提交快照；其他地址的验证状态不能套用到该邮箱。
    applySettings({ ...data, email: submittedEmail, email_verified: data.email === submittedEmail && data.email_verified === true })
    notice.value = !savedEmail.value ? '个人邮箱已清除。' : emailVerified.value ? '个人邮箱已保存，已验证。' : '个人邮箱已保存，请验证后接收邮件。'
  } catch (e) { if (id === requestId) error.value = e.message }
  finally { if (id === requestId) busy.value = false }
}
async function requestCode() {
  if (!canVerify.value) return
  const id = ++requestId
  busy.value = true; error.value = ''; notice.value = ''
  try {
    const data = await mutateJson('/api/me/email-verification/request', 'POST', {})
    if (id !== requestId) return
    if (data.status === 'sent') { code.value = ''; notice.value = `验证码已发送至 ${savedEmail.value}，请查看收件箱。` }
    else if (data.status === 'pending_configuration') notice.value = '邮件服务待配置，暂时无法发送验证码。请联系站长。'
    else error.value = data.message || data.detail || '验证码未发送成功，请稍后重试。'
  } catch (e) { if (id === requestId) error.value = e.message }
  finally { if (id === requestId) busy.value = false }
}
async function confirmCode() {
  if (!canVerify.value) return
  if (!/^[0-9]{8}$/.test(code.value)) { error.value = '请输入邮件中的 8 位数字验证码。'; return }
  const id = ++requestId
  busy.value = true; error.value = ''; notice.value = ''
  try {
    const data = await mutateJson('/api/me/email-verification/confirm', 'POST', { code: code.value })
    if (id !== requestId) return
    applySettings(data); notice.value = '邮箱已验证，可以接收自己的行情提醒和测试邮件。'
  } catch (e) { if (id === requestId) error.value = e.message }
  finally { if (id === requestId) busy.value = false }
}
onMounted(load)
onBeforeUnmount(() => { requestId++ })
</script>
<template>
  <h1 class="page-title">我的推送设置</h1>
  <section class="panel alert-form-panel">
    <form class="account-form" @submit.prevent="save"><label>收件邮箱<input v-model="email" @input="editVersion++" type="email" maxlength="254" placeholder="your@example.com" :disabled="loading || busy || !loaded" /></label><button class="button" :disabled="loading || busy || !loaded">{{ loading ? '正在读取…' : '保存邮箱' }}</button></form>
    <p v-if="loaded" data-testid="email-verification-status">{{ !savedEmail ? '尚未设置收件邮箱' : emailVerified ? '已保存邮箱：已验证' : '已保存邮箱：未验证' }}<span v-if="dirty">；修改尚未保存，请先保存再验证。</span></p>
    <template v-if="loaded && savedEmail && !emailVerified">
      <p>验证邮箱归属后才会发送行情提醒和测试邮件。修改邮箱后需要重新验证。</p>
      <button class="text-button" :disabled="!canVerify" @click="requestCode">发送验证码</button>
      <form class="account-form" @submit.prevent="confirmCode"><label>邮箱验证码<input v-model="code" type="text" inputmode="numeric" autocomplete="one-time-code" pattern="[0-9]{8}" minlength="8" maxlength="8" placeholder="8 位数字" required :disabled="!canVerify" /></label><button class="button" :disabled="!canVerify || !/^[0-9]{8}$/.test(code)">确认验证码</button></form>
    </template>
    <p v-if="channel">{{ channel.message }}</p><p>普通账号通过已验证邮箱接收；管理员的 Telegram 推送不受邮箱验证影响，验证邮箱后可同时接收邮件。</p>
    <p>“发送成功”表示邮件服务已接受，请在邮箱核对收信。<RouterLink to="/push-logs">查看我的推送日志 ↗</RouterLink></p>
  </section>
  <div v-if="error" class="notice error-notice" role="alert">{{ error }}<button v-if="!loaded" class="text-button" :disabled="loading" @click="load">重新读取</button></div><div v-if="notice" class="notice" role="status">{{ notice }}</div>
</template>
