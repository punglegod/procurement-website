import { ref } from 'vue'
import { getJson, mutateJson } from './api'
export const user = ref(null)
export async function loadUser() {
  try { user.value = await getJson('/api/auth/me'); return user.value }
  catch { user.value = null; return null }
}
export async function logout() {
  await mutateJson('/api/auth/logout')
  user.value = null
  window.location.assign('/login')
}
