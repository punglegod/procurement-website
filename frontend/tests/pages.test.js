import { afterEach, beforeAll, describe, expect, test, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createRouter, createMemoryHistory } from 'vue-router'
import { readFileSync, mkdirSync, writeFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { useMarket } from '../src/store'
import MarketView from '../src/views/MarketView.vue'
import DetailView from '../src/views/DetailView.vue'

// 该文件由只读服务验收脚本生成，内容全部来自第一阶段项目数据库。
const live = JSON.parse(readFileSync(resolve('../data/stage2-live.json'), 'utf8'))
const market = useMarket()
const wrappers = []
const width = 1000
const height = 355

beforeAll(() => {
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} })
  Object.defineProperty(HTMLElement.prototype, 'clientWidth', { configurable: true, get: () => width })
  Object.defineProperty(HTMLElement.prototype, 'clientHeight', { configurable: true, get: () => height })
  // SVG 渲染只需字体测宽；JSDOM 不提供真实画布。
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(() => ({ measureText: text => ({ width: String(text).length * 7 }) }))
})

afterEach(() => {
  wrappers.splice(0).forEach(wrapper => wrapper.unmount())
  vi.unstubAllGlobals()
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} })
})

async function render(component, path = '/') {
  market.items.value = structuredClone(live.latest.items)
  market.refreshedAt.value = new Date().toISOString()
  market.loading.value = false
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: MarketView }, { path: '/commodity/:code', component: DetailView }] })
  await router.push(path)
  await router.isReady()
  const wrapper = mount(component, { global: { plugins: [router] } })
  wrappers.push(wrapper)
  await flushPromises()
  return wrapper
}

describe('真实入库行情页面', () => {
  test('内容工具栏保留可用刷新与搜索，页面无英雄区或装饰眉题', async () => {
    const requests = vi.fn(async () => ({ ok: true, json: async () => live.latest }))
    vi.stubGlobal('fetch', requests)
    const wrapper = await render(MarketView)
    const toolbar = wrapper.get('.market-panel .panel-toolbar')
    expect(toolbar.find('input[type=search]').exists()).toBe(true)
    const refresh = toolbar.findAll('button').find(button => button.text() === '刷新数据')
    expect(refresh).toBeDefined()
    await refresh.trigger('click'); await flushPromises()
    expect(requests).toHaveBeenCalled()
    expect(wrapper.find('.page-heading, .eyebrow').exists()).toBe(false)
    expect(wrapper.text()).not.toMatch(/SPOT MARKET|页面读取于|让采购有据可循/)
  })
  test('首页展示铝锭真实价格、口径、日期、来源及缺失涨跌', async () => {
    const wrapper = await render(MarketView)
    const row = wrapper.findAll('tbody tr')[0]
    const point = live.latest.items[0].price_point
    expect(point.price).toBeGreaterThan(0)
    expect(row.text()).toContain(point.price.toLocaleString('zh-CN'))
    expect(row.text()).toContain(point.quote_basis)
    expect(row.text()).toContain(point.quote_date)
    expect(row.get('.source-cell a').attributes('href')).toBe(point.source_url)
    const change = live.latest.items[0].change_amount
    expect(row.findAll('td')[1].text()).toBe(change == null ? '—' : `${change > 0 ? '+' : change < 0 ? '−' : ''}${Math.abs(change).toLocaleString('zh-CN')}`)
    expect(wrapper.findAll('tbody tr')).toHaveLength(11)
    await wrapper.get('input').setValue('alu_a00')
    expect(wrapper.findAll('tbody tr')).toHaveLength(1)
    await wrapper.get('input').setValue('no-such-commodity')
    expect(wrapper.text()).toContain('没有符合条件的品种')
  })

  test('过期报价标黄；无数据品种保留空值和待配置状态', async () => {
    const wrapper = await render(MarketView)
    // 隔离模拟首次启动无数据；真实全站响应在本阶段已有全部品种报价。
    market.items.value[1].price_point = null
    market.items.value[1].data_status = 'failed'
    market.items.value[1].message = '公开源网络失败'
    market.items.value[2].price_point = null
    market.items.value[2].data_status = 'never_run'
    market.items.value[0].stale = true
    await flushPromises()
    expect(wrapper.findAll('tbody tr')[0].classes()).toContain('stale-row')
    expect(wrapper.findAll('tbody tr')[0].text()).toContain('过期 · 非今日报价')
    expect(wrapper.findAll('tbody tr')[1].text()).toContain('来源异常')
    expect(wrapper.findAll('tbody tr')[1].text()).toContain('公开源网络失败')
    expect(wrapper.findAll('tbody tr')[2].text()).toContain('暂无入库报价')
    await wrapper.findAll('.filter-tabs button')[2].trigger('click')
    expect(wrapper.findAll('tbody tr')).toHaveLength(2)
  })

  test('详情页实际渲染 ECharts SVG；7/30/90 天切换保留真实单点', async () => {
    const calls = []
    vi.stubGlobal('fetch', vi.fn(async path => {
      calls.push(path)
      return { ok: true, json: async () => live.history[new URL(path, 'http://localhost').searchParams.get('days')] }
    }))
    const wrapper = await render(DetailView, '/commodity/alu_a00')
    expect(wrapper.find('.price-chart svg').exists()).toBe(true)
    expect(wrapper.text()).toContain('仅有 1 个报价点')
    expect(wrapper.get('.price-chart').attributes('aria-label')).toContain('共 1 条真实报价')
    for (const days of [7, 90, 30]) {
      const button = wrapper.findAll('.range-tabs button').find(button => button.text() === `${days} 天`)
      await button.trigger('click')
      await flushPromises()
      expect(calls.at(-1)).toContain(`days=${days}`)
      expect(button.attributes('aria-pressed')).toBe('true')
      expect(wrapper.find('.price-chart svg').exists()).toBe(true)
    }
    const svg = wrapper.get('.price-chart svg').element.outerHTML
    expect(svg).toContain(live.latest.items[0].price_point.quote_date)
    expect(svg).toContain('path')
    mkdirSync(resolve('../data/stage2-qa'), { recursive: true })
    writeFileSync(resolve('../data/stage2-qa/alu-a00-chart.svg'), svg)
  })

  test('空历史、请求失败和重试均有明确反馈', async () => {
    let failure = false
    vi.stubGlobal('fetch', vi.fn(async () => failure ? { ok: false, status: 503 } : { ok: true, json: async () => ({ items: [] }) }))
    const wrapper = await render(DetailView, '/commodity/pc')
    expect(wrapper.text()).toContain('这段时间暂无报价')
    expect(wrapper.find('svg').exists()).toBe(false)
    failure = true
    await wrapper.findAll('.range-tabs button')[0].trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('HTTP 503')
    failure = false
    await wrapper.get('.chart-placeholder button').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('这段时间暂无报价')
  })

  test('快速切换时间范围时，迟到的旧响应不能覆盖新图表', async () => {
    let resolveOld
    vi.stubGlobal('fetch', vi.fn(path => path.endsWith('days=30')
      ? new Promise(resolve => { resolveOld = resolve })
      : Promise.resolve({ ok: true, json: async () => ({ items: [] }) })))
    const wrapper = await render(DetailView, '/commodity/alu_a00')
    await wrapper.findAll('.range-tabs button')[0].trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('这段时间暂无报价')
    resolveOld({ ok: true, json: async () => live.history['30'] })
    await flushPromises()
    expect(wrapper.text()).toContain('这段时间暂无报价')
    expect(wrapper.find('svg').exists()).toBe(false)
  })
})
