export async function getJson(path, signal) {
  const response = await fetch(path, { signal, headers: { Accept: 'application/json' } })
  if (!response.ok) throw new Error(`请求失败（HTTP ${response.status}），请稍后重试。`)
  return response.json()
}

export const number = (value) => value == null ? '—' : new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 2 }).format(value)
export const signed = (value, suffix = '') => value == null ? '—' : `${value > 0 ? '+' : value < 0 ? '−' : ''}${number(Math.abs(value))}${suffix}`
export const direction = (value) => value > 0 ? 'up' : value < 0 ? 'down' : 'neutral'
export const time = (value) => value ? new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }).format(new Date(value)) : '—'

export function statusText(item) {
  if (item.price_point) return item.stale ? '数据过期' : '今日报价'
  if (item.data_status === 'pending_credentials') return '待配置凭据'
  if (item.data_status === 'pending_configuration') return '免费源待接入'
  if (['failed', 'blocked', 'interrupted'].includes(item.data_status)) return '来源异常'
  return '暂无报价'
}

export function sourceUrl(value) {
  try {
    const url = new URL(value)
    return ['https:', 'http:'].includes(url.protocol) ? url.href : null
  } catch { return null }
}
