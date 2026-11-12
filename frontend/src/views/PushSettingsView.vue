<script setup>
import { onMounted, ref } from 'vue'
import { getJson, mutateJson } from '../api'
const email = ref(''), channel = ref(null), error = ref(''), notice = ref(''), busy = ref(false)
async function load() { const data = await getJson('/api/me/push-settings'); email.value = data.email; channel.value = data.channel }
async function save() {
  busy.value = true; error.value = ''; notice.value = ''
  try { const data = await mutateJson('/api/me/push-settings', 'PUT', { email: email.value }); channel.value = data.channel; notice.value = '个人邮箱已保存。' }
  catch (e) { error.value = e.message } finally { busy.value = false }
}
onMounted(async () => { try { await load() } catch (e) { error.value = e.message } })
</script>
<template>
  <section class="page-heading"><div><div class="eyebrow">MY NOTIFICATIONS</div><h1>我的推送设置</h1><p>仅接收自己规则触发的现货提醒。</p></div></section>
  <section class="panel alert-form-panel">
    <form class="account-form" @submit.prevent="save"><label>收件邮箱<input v-model="email" type="email" maxlength="254" placeholder="your@example.com" /></label><button class="button" :disabled="busy">保存邮箱</button></form>
    <p v-if="channel">{{ channel.message }}</p><p>普通账号通过邮箱接收；管理员继续通过 Telegram 接收，填写邮箱后同时接收邮件。</p>
    <p>“发送成功”表示邮件服务已接受，请在邮箱核对收信。<RouterLink to="/push-logs">查看我的推送日志 ↗</RouterLink></p>
  </section>
  <div v-if="error" class="notice error-notice" role="alert">{{ error }}</div><div v-if="notice" class="notice" role="status">{{ notice }}</div>
</template>
