<script setup>
import { ref } from 'vue'
import { mutateJson } from '../api'
const oldPassword = ref(''), newPassword = ref(''), error = ref(''), busy = ref(false)
async function save() {
  busy.value = true; error.value = ''
  try { await mutateJson('/api/auth/password', 'POST', { old_password: oldPassword.value, new_password: newPassword.value }); window.location.assign('/login') }
  catch (e) { error.value = e.message } finally { busy.value = false }
}
</script>
<template>
  <h1 class="page-title">账号与密码</h1>
  <section class="panel alert-form-panel"><p>修改密码后，所有设备需重新登录。</p><form class="account-form" @submit.prevent="save">
    <label>原密码<input v-model="oldPassword" type="password" required autocomplete="current-password" maxlength="128" /></label>
    <label>新密码<input v-model="newPassword" type="password" required minlength="8" maxlength="128" autocomplete="new-password" /></label>
    <button class="button" :disabled="busy">修改密码并重新登录</button>
  </form><p v-if="error" class="notice error-notice" role="alert">{{ error }}</p></section>
</template>
