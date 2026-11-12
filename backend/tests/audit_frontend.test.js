import { afterEach, expect, test, vi } from '../../frontend/node_modules/vitest/dist/index.js'
import { flushPromises, mount } from '../../frontend/node_modules/@vue/test-utils/dist/vue-test-utils.esm-bundler.mjs'
import PushSettingsView from '../../frontend/src/views/PushSettingsView.vue'
import PushLogsView from '../../frontend/src/views/PushLogsView.vue'
import AlertsView from '../../frontend/src/views/AlertsView.vue'
import { sourceUrl } from '../../frontend/src/api.js'

// 全部请求使用隔离内存响应，不连接生产后端、不发送邮件。
let wrapper
afterEach(() => { wrapper?.unmount(); vi.unstubAllGlobals(); vi.restoreAllMocks() })
const channel = { status: 'ready', message: '审计隔离通道' }
const response = data => ({ ok: true, json: async () => data })
function render(component) { return mount(component, { global: { stubs: { RouterLink: { template: '<a><slot /></a>' } } } }) }

test('STAGE7 F1 初始慢读取期间禁止保存，晚到响应不会覆盖编辑', async () => {
  let finishLoad
  let stored = 'old@example.com'
  vi.stubGlobal('fetch', vi.fn(async (path, options = {}) => {
    if (options.method === 'PUT') {
      stored = JSON.parse(options.body).email
      return response({ email: stored, email_verified: false, channel })
    }
    const snapshot = stored
    return await new Promise(resolve => { finishLoad = () => resolve(response({ email: snapshot, channel })) })
  }))
  wrapper = render(PushSettingsView)
  expect(wrapper.get('button').attributes('disabled')).toBeDefined()
  expect(wrapper.get('input[type=email]').attributes('disabled')).toBeDefined()
  // 即使绕过禁用控件人为触发输入/提交，也不能在未读取配置前保存。
  const input = wrapper.get('input[type=email]').element
  input.value = 'new@example.com'
  input.dispatchEvent(new Event('input', { bubbles: true }))
  await wrapper.get('form').trigger('submit'); await flushPromises()
  expect(stored).toBe('old@example.com')
  finishLoad(); await flushPromises()
  expect(wrapper.get('input[type=email]').element.value).toBe('new@example.com')
  await wrapper.get('form').trigger('submit'); await flushPromises()
  expect(stored).toBe('new@example.com')
  expect(wrapper.get('input[type=email]').element.value).toBe('new@example.com')
  expect(wrapper.get('[role=status]').text()).toContain('已保存')
  console.log('F1 FIXED: initial_save_blocked=true; delayed_GET_preserves_edit=true; saved_and_displayed=new@example.com')
})

test('STAGE7 F2 翻页失败保留原页码和原页数据', async () => {
  vi.stubGlobal('fetch', vi.fn(async path => {
    if (path.includes('status')) return response(channel)
    if (path.endsWith('page=2')) return { ok: false, status: 503 }
    return response({ total: 31, items: [{ id: 31, kind: 'test', channel: 'email', status: 'sent', detail: '第 1 页记录', message: 'PAGE_ONE_MESSAGE', created_at: '2026-11-12T09:00:00+08:00' }] })
  }))
  wrapper = render(PushLogsView); await flushPromises()
  await wrapper.findAll('button').find(b => b.text() === '下一页').trigger('click'); await flushPromises()
  expect(wrapper.get('.table-footer').text()).toContain('第 1 页')
  expect(wrapper.get('tbody').text()).toContain('PAGE_ONE_MESSAGE')
  expect(wrapper.get('[role=alert]').text()).toContain('HTTP 503')
  expect(wrapper.findAll('button').find(b => b.text() === '下一页').attributes('disabled')).toBeUndefined()
  console.log('F2 FIXED: failed_page=2; footer=第 1 页; rows=PAGE_ONE_MESSAGE; retry_enabled=true')
})

test('AUDIT XSS 日志/规则 HTML 按文本转义，危险链接被拦截', async () => {
  const payload = '<img src=x onerror="window.auditXss=1">'
  vi.stubGlobal('fetch', vi.fn(async path => response(path.includes('commodities') ? { items: [] } : {
    items: [{ id: 1, name: payload, quote_basis: payload, data_message: payload, kind: 'price_gte', threshold: 1, unit: '元/吨', enabled: true }], channel: { message: payload },
  })))
  wrapper = render(AlertsView); await flushPromises()
  expect(wrapper.findAll('img')).toHaveLength(0)
  expect(wrapper.text()).toContain(payload)
  for (const url of ['javascript:alert(1)', 'data:text/html,<script>alert(1)</script>', 'file:///etc/passwd', 'vbscript:msgbox(1)']) expect(sourceUrl(url)).toBeNull()
  expect(sourceUrl('https://example.com/quote')).toBe('https://example.com/quote')
  console.log('XSS: inserted_html_elements=0; rejected_protocols=javascript,data,file,vbscript')
})
