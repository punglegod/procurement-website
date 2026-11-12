import { afterEach, expect, test, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import AlertsView from '../src/views/AlertsView.vue'
import PushLogsView from '../src/views/PushLogsView.vue'

let wrapper
afterEach(() => { wrapper?.unmount(); vi.unstubAllGlobals(); vi.restoreAllMocks() })
const pending = { status: 'pending_configuration', message: '待配置：TELEGRAM_BOT_TOKEN、TELEGRAM_CHAT_ID' }
const commodity = { code: 'alu_a00', name: '南海灵通 A00 铝锭', unit: '元/吨' }
function render(component) { return mount(component, { global: { stubs: { RouterLink: { template: '<a><slot /></a>' } } } }) }
function button(text) { return wrapper.findAll('button').find(b => b.text() === text) }

test('页面保存、编辑、暂停、删除规则；待配置检查结果如实展示', async () => {
  let rules = [], next = 1
  vi.spyOn(window, 'scrollTo').mockImplementation(() => {})
  const requests = []
  vi.stubGlobal('fetch', vi.fn(async (path, options = {}) => {
    requests.push([path, options])
    const body = options.body && JSON.parse(options.body)
    const id = Number(path.split('/').at(-1))
    if (path === '/api/commodities') return { ok: true, json: async () => ({ items: [commodity] }) }
    if (path === '/api/alerts/check') return { ok: true, json: async () => ({ results: [{ rule_id: 1, status: 'pending_configuration', detail: '待配置' }] }) }
    if (options.method === 'POST') rules.push({ ...commodity, ...body, id: next++, quote_basis: '固定测试口径', data_message: '暂无同口径报价，等待数据' })
    if (options.method === 'PUT') rules = rules.map(r => r.id === id ? { ...r, ...body } : r)
    if (options.method === 'DELETE') rules = rules.filter(r => r.id !== id)
    return { ok: true, json: async () => ({ items: structuredClone(rules), channel: pending }) }
  }))
  wrapper = render(AlertsView); await flushPromises()
  expect(wrapper.text()).toContain('待配置')
  await wrapper.get('form').trigger('submit'); await flushPromises()
  expect(wrapper.findAll('tbody tr')).toHaveLength(1)
  expect(wrapper.text()).toContain('规则已保存')
  await button('编辑').trigger('click'); await flushPromises()
  await wrapper.get('input[type=number]').setValue('2.5')
  await wrapper.get('form').trigger('submit'); await flushPromises()
  expect(requests.filter(r => r[1].method === 'PUT').at(-1)[1].body).toContain('2.5')
  await button('暂停').trigger('click'); await flushPromises()
  expect(wrapper.text()).toContain('已暂停')
  await button('立即检查规则').trigger('click'); await flushPromises()
  expect(wrapper.get('[role=status]').text()).toContain('待配置')
  await button('删除').trigger('click'); await flushPromises()
  expect(wrapper.findAll('tbody tr')).toHaveLength(0)
})

test('测试推送按钮显示待配置并刷新日志，不显示发送成功', async () => {
  let logs = []
  vi.stubGlobal('fetch', vi.fn(async (path, options = {}) => {
    let result
    if (path === '/api/alerts/status') result = pending
    else if (options.method === 'POST') {
      result = { id: 1, kind: 'test', status: 'pending_configuration', detail: pending.message, message: '隔离测试消息', created_at: new Date().toISOString() }
      logs = [result]
    } else result = { items: logs, total: logs.length }
    return { ok: true, json: async () => result }
  }))
  wrapper = render(PushLogsView); await flushPromises()
  await button('发送测试推送').trigger('click'); await flushPromises()
  expect(wrapper.get('[role=status]').text()).toContain('待配置')
  expect(wrapper.findAll('tbody tr')).toHaveLength(1)
  expect(wrapper.get('tbody').text()).not.toContain('发送成功')
})

test('推送失败和接口异常有可读反馈，按钮恢复可用', async () => {
  vi.stubGlobal('fetch', vi.fn(async (path, options = {}) => {
    if (options.method === 'POST') return { ok: false, status: 503, json: async () => ({ detail: '通道暂不可用' }) }
    return { ok: true, json: async () => path.includes('status') ? pending : { items: [], total: 0 } }
  }))
  wrapper = render(PushLogsView); await flushPromises()
  await button('发送测试推送').trigger('click'); await flushPromises()
  expect(wrapper.get('[role=alert]').text()).toContain('通道暂不可用')
  expect(button('发送测试推送').attributes('disabled')).toBeUndefined()
})
