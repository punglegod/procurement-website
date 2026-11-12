<script setup>
import { computed, ref } from 'vue'
import { useRoute } from 'vue-router'
import { mutateJson } from '../api'
const route = useRoute()
const registering = computed(() => route.path === '/register')
const username = ref(''), password = ref(''), error = ref(''), notice = ref(''), busy = ref(false)
async function submit() {
  busy.value = true; error.value = ''; notice.value = ''
  try {
    const data = { username: username.value, password: password.value }
    if (registering.value) {
      const result = await mutateJson('/api/auth/register', 'POST', data)
      notice.value = result.message; password.value = ''; return
    }
    await mutateJson('/api/auth/login', 'POST', data)
    window.location.assign('/')
  } catch (e) { error.value = e.message } finally { busy.value = false }
}
</script>
<template>
  <main class="auth-shell"><section class="panel auth-card">
    <h1>{{ registering ? '创建账号' : '登录采购行情' }}</h1>
    <p>登录后查看 11 个品种的现货行情，管理自己的价格提醒。</p>
    <form class="account-form" @submit.prevent="submit">
      <label>账号<input v-model="username" required minlength="3" maxlength="64" pattern="[A-Za-z0-9_\-]+" autocomplete="username" placeholder="3～64 位字母、数字、下划线或连字符" /></label>
      <label>密码<input v-model="password" type="password" required minlength="8" maxlength="128" :autocomplete="registering ? 'new-password' : 'current-password'" placeholder="至少 8 位" /></label>
      <div v-if="error" class="notice error-notice" role="alert">{{ error }}</div>
      <button class="button" :disabled="busy">{{ busy ? '处理中…' : registering ? '注册' : '登录' }}</button>
    </form>
    <p v-if="notice" class="notice" role="status">{{ notice }}</p>
    <RouterLink :to="registering ? '/login' : '/register'" class="text-button">{{ registering ? '已有账号，去登录' : '注册普通账号' }}</RouterLink>
  </section></main>
</template>
