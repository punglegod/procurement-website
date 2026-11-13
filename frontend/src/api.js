export async function getJson(path, signal) {
  const response = await fetch(path, { signal, headers: { Accept: 'application/json' } })
  if (response.status === 401 && path !== '/api/auth/me') window.location.assign('/login')
  if (!response.ok) throw new Error(`请求失败（HTTP ${response.status}），请稍后重试。`)
  return response.json()
}

export async function mutateJson(path, method = 'POST', body) {
  const response = await fetch(path, { method, headers: { 'Content-Type': 'application/json', Accept: 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body) })
  if (response.status === 401 && !path.startsWith('/api/auth/')) window.location.assign('/login')
  if (!response.ok) {
    const data = await response.json().catch(() => ({}))
    throw new Error(typeof data.detail === 'string' ? data.detail : `保存或操作失败（HTTP ${response.status}），请检查输入后重试。`)
  }
  return response.json()
}

export const pushStatus = value => ({ sent: '发送成功', failed: '发送失败', unknown: '送达未知', sending: '处理中 / 中断后送达未知', pending_configuration: '待配置', pending_verification: '待验证邮箱', deduplicated: '今日已尝试', skipped: '未触发 / 等待数据' }[value] || value)

export const number = (value) => value == null ? '—' : new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 2 }).format(value)
export const signed = (value, suffix = '') => value == null ? '—' : `${value > 0 ? '+' : value < 0 ? '−' : ''}${number(Math.abs(value))}${suffix}`
export const direction = (value) => value > 0 ? 'up' : value < 0 ? 'down' : 'neutral'
export const pushKind = log => ({ test: '通道测试', verification: '邮箱验证', digest: '日报', fetch_progress: '抓取进度' }[log.kind] || `规则 #${log.rule_id} · ${log.code}`)
export const time = (value) => value ? new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }).format(new Date(value)) : '—'

export function statusText(item) {
  if (item.price_point) return item.stale ? '数据过期' : '有效报价'
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
