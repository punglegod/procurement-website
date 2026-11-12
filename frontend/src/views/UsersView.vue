<script setup>
import { onMounted, ref } from 'vue'
import { getJson, mutateJson } from '../api'
const users = ref([]), enabled = ref(true), error = ref(''), busy = ref(false), removing = ref(null)
async function load() { const data = await getJson('/api/admin/users'); users.value = data.items; enabled.value = data.registration_enabled }
async function action(callback) { busy.value = true; error.value = ''; try { await callback(); await load() } catch (e) { error.value = e.message } finally { busy.value = false } }
onMounted(() => action(async () => {}))
</script>
<template>
  <section class="page-heading"><div><h1>用户管理</h1><p>普通账号只能管理自己的提醒和邮箱。</p></div><button class="button" :disabled="busy" @click="action(() => mutateJson('/api/admin/registration', 'PUT', { enabled: !enabled }))">{{ enabled ? '关闭注册' : '开放注册' }}</button></section>
  <p v-if="error" class="notice error-notice" role="alert">{{ error }}</p>
  <section class="panel"><div class="alert-table-wrap"><table class="alert-table"><thead><tr><th>账号</th><th>角色</th><th>状态</th><th>操作</th></tr></thead><tbody>
    <tr v-for="u in users" :key="u.id"><td>{{ u.username }}</td><td>{{ u.role }}</td><td>{{ u.enabled ? '正常' : '已禁用' }}</td><td v-if="u.role !== 'admin'">
      <button class="text-button" :disabled="busy" @click="action(() => mutateJson(`/api/admin/users/${u.id}`, 'PUT', { enabled: !u.enabled }))">{{ u.enabled ? '禁用' : '启用' }}</button>
      <button v-if="removing !== u.id" class="text-button" :disabled="busy" @click="removing = u.id">删除</button>
      <template v-else><span>删除该账号及其规则、设置、日志？</span><button class="text-button" :disabled="busy" @click="action(async () => { await mutateJson(`/api/admin/users/${u.id}`, 'DELETE'); removing = null })">确认删除</button><button class="text-button" @click="removing = null">取消</button></template>
    </td><td v-else>管理员账号受保护</td></tr>
  </tbody></table></div></section>
</template>
