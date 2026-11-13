import { afterEach, expect, test, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import SettingsView from '../src/views/SettingsView.vue'

let wrapper
afterEach(() => { wrapper?.unmount(); vi.unstubAllGlobals(); vi.restoreAllMocks() })
const catalog = [{ code: 'alu_a00', name: 'A00铝锭' }, { code: 'abs', name: 'ABS' }, { code: 'pc', name: 'PC' }]
const ok = data => ({ ok: true, json: async () => data })
function render(handler) {
  const fetch = vi.fn(async (path, options = {}) => {
    if (options.method === 'POST') return handler(path, options)
    return ok(path === '/api/commodities' ? { items: catalog } : path === '/api/admin/fetch-settings' ? { progress_enabled: true } : { items: [], message: '隔离状态' })
  })
  vi.stubGlobal('fetch', fetch)
  wrapper = mount(SettingsView, { global: { stubs: { Sci99Settings: true, RouterLink: { template: '<a><slot /></a>' } } } })
  return fetch
}
const button = () => wrapper.findAll('button').find(b => b.text() === '立即抓取行情')

test('手动勾选只提交选中品类，逐项显示成功和兜底结果，重读不重置选择', async () => {
  const fetch = render(async () => ok({ status: 'partial', commodity_results: [{ code: 'pc', outcome: 'success', message: '卓创异常；免费兜底：已入库' }] }))
  await flushPromises()
  const boxes = wrapper.findAll('fieldset input')
  expect(boxes).toHaveLength(3)
  await boxes[0].setValue(false); await boxes[1].setValue(false)
  await button().trigger('click'); await flushPromises()
  const [path, request] = fetch.mock.calls.find(([, options]) => options.method === 'POST')
  expect(path).toBe('/api/fetch/run')
  expect(JSON.parse(request.body)).toEqual({ codes: ['pc'] })
  expect(wrapper.text()).toContain('PC · 成功 · 卓创异常；免费兜底：已入库')
  expect(wrapper.findAll('fieldset input').map(b => b.element.checked)).toEqual([false, false, true])
  for (const text of ['16:30', '09:00轮结束后', '今日已抓取', '手动不限次']) expect(wrapper.text()).toContain(text)
})

test('加载完成前和未勾选时禁用抓取，不发送空选择', async () => {
  const fetch = render(async () => ok({}))
  expect(button().attributes('disabled')).toBeDefined()
  await flushPromises()
  for (const box of wrapper.findAll('fieldset input')) await box.setValue(false)
  expect(button().attributes('disabled')).toBeDefined()
  await button().trigger('click')
  expect(fetch.mock.calls.some(([, options]) => options.method === 'POST')).toBe(false)
})

test('执行期间锁定品类且失败后恢复原勾选，提示错误', async () => {
  let finish
  render(() => new Promise(resolve => { finish = resolve })); await flushPromises()
  await wrapper.findAll('fieldset input')[0].setValue(false)
  await button().trigger('click')
  expect(wrapper.get('fieldset').attributes('disabled')).toBeDefined()
  finish({ ok: false, status: 409, json: async () => ({ detail: '抓取任务正在执行' }) }); await flushPromises()
  expect(wrapper.get('[role=alert]').text()).toContain('抓取任务正在执行')
  expect(wrapper.findAll('fieldset input').map(b => b.element.checked)).toEqual([false, true, true])
  expect(button().attributes('disabled')).toBeUndefined()
})
