import { computed, ref } from 'vue'
import { getJson } from './api'

const items = ref([])
const sources = ref([])
const loading = ref(false)
const error = ref('')
const refreshedAt = ref(null)
let request = null

async function refresh() {
  if (request) return request
  loading.value = true
  error.value = ''
  request = (async () => {
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), 15000)
    try {
      const [latest, status] = await Promise.all([
        getJson('/api/prices/latest', controller.signal),
        getJson('/api/sources/status', controller.signal),
      ])
      items.value = latest.items
      sources.value = status.items
      refreshedAt.value = new Date().toISOString()
    } catch (cause) {
      error.value = cause.name === 'AbortError' ? '请求超时，请检查本地服务后重试。' : cause.message
    } finally {
      clearTimeout(timer)
      loading.value = false
      request = null
    }
  })()
  return request
}

export function useMarket() {
  return {
    items, sources, loading, error, refreshedAt, refresh,
    quoted: computed(() => items.value.filter(item => item.price_point).length),
    stale: computed(() => items.value.filter(item => item.price_point && item.stale).length),
  }
}
