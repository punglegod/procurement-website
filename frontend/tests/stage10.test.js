import { afterEach, expect, test, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import QuoteSelector from '../src/components/QuoteSelector.vue'
import { quotePrice } from '../src/api'

let wrapper
afterEach(() => { wrapper?.unmount(); vi.unstubAllGlobals(); vi.restoreAllMocks() })
const response = data => ({ ok: true, json: async () => data })
const entries = [
  { id: 1, code: 'paper', product_name: '瓦楞纸', specification: 'AA级高瓦120g', market: '华南', price_type: '市场价格', source_name: '卓创资讯', missing: true, price_point: { price: 3065, low: 2980, high: 3150, price_text: '2980-3150', unit: '元/吨', quote_date: '2026-11-13', change_text: '+10' } },
  { id: 2, code: 'paper', product_name: '瓦楞纸', specification: '<img src=x onerror=alert(1)>', market: '华北', price_type: '均价', source_name: '卓创资讯', price_point: { price: 3500, unit: '元/吨', quote_date: '2026-11-13' } },
]

test('全部报价保留区间、单位、分组和缺失状态，当前口径高亮，文本转义', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => response({ items: entries, selected_entry_id: 1 })))
  wrapper = mount(QuoteSelector, { props: { code: 'paper', selectedId: 1 } }); await flushPromises()
  expect(wrapper.findAll('tbody tr')).toHaveLength(2)
  expect(wrapper.get('tr.selected-quote-row').text()).toContain('2980-3150')
  for (const fact of ['元/吨', '市场价格', '均价', '华南', 'AA级高瓦120g', '本轮未更新']) expect(wrapper.text()).toContain(fact)
  expect(wrapper.find('img').exists()).toBe(false)
  expect(wrapper.text()).toContain('<img src=x')
})

test('选中即保存，只提交本账号条目，成功后才更新高亮', async () => {
  let finish
  const fetch = vi.fn(async (path, options = {}) => options.method === 'PUT'
    ? new Promise(resolve => { finish = resolve }) : response({ items: entries, selected_entry_id: 1 }))
  vi.stubGlobal('fetch', fetch)
  wrapper = mount(QuoteSelector, { props: { code: 'paper', selectedId: 1 } }); await flushPromises()
  await wrapper.get('select').setValue(2)
  expect(wrapper.get('select').attributes('disabled')).toBeDefined()
  expect(wrapper.get('tr.selected-quote-row').text()).toContain('华南')
  const [path, options] = fetch.mock.calls.at(-1)
  expect(path).toBe('/api/me/quote-selections/paper')
  expect(JSON.parse(options.body)).toEqual({ entry_id: 2 })
  finish(response({ items: entries, selected_entry_id: 2 })); await flushPromises()
  expect(wrapper.get('tr.selected-quote-row').text()).toContain('华北')
  expect(wrapper.emitted('saved')).toHaveLength(1)
})

test('保存失败保留原口径和高亮并提示', async () => {
  vi.stubGlobal('fetch', vi.fn(async (path, options = {}) => options.method === 'PUT'
    ? { ok: false, status: 503, json: async () => ({ detail: '服务暂不可用' }) } : response({ items: entries, selected_entry_id: 1 })))
  wrapper = mount(QuoteSelector, { props: { code: 'paper', selectedId: 1 } }); await flushPromises()
  await wrapper.get('select').setValue(2); await flushPromises()
  expect(wrapper.get('select').element.value).toBe('1')
  expect(wrapper.get('tr.selected-quote-row').text()).toContain('华南')
  expect(wrapper.get('[role=alert]').text()).toContain('服务暂不可用')
  expect(wrapper.emitted('saved')).toBeUndefined()
})

test('切换品种后旧请求和旧保存不覆盖新页面', async () => {
  let finish
  vi.stubGlobal('fetch', vi.fn(async (path, options = {}) => options.method === 'PUT'
    ? new Promise(resolve => { finish = resolve }) : response(path.includes('code=paper') ? { items: entries, selected_entry_id: 1 } : { items: [], selected_entry_id: null })))
  wrapper = mount(QuoteSelector, { props: { code: 'paper', selectedId: 1 } }); await flushPromises()
  await wrapper.get('select').setValue(2)
  await wrapper.setProps({ code: 'abs', selectedId: null }); await flushPromises()
  finish(response({ items: entries, selected_entry_id: 2 })); await flushPromises()
  expect(wrapper.findAll('tbody tr')).toHaveLength(1)
  expect(wrapper.text()).toContain('暂无入库报价')
  expect(wrapper.emitted('saved')).toBeUndefined()
})

test('区间展示使用原文，禁止仅显示中值', () => {
  expect(quotePrice(entries[0].price_point)).toBe('2980-3150')
  expect(quotePrice({ price: 3065, low: 2980, high: 3150 })).toBe('2,980-3,150')
  expect(quotePrice(null)).toBe('—')
  expect(quotePrice({ price: 0.1234, price_text: '0.1234' })).toBe('0.1234')
})
