import { afterEach, expect, test, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import Sci99Settings from '../src/components/Sci99Settings.vue'

let wrapper
afterEach(() => { wrapper?.unmount(); vi.unstubAllGlobals(); vi.restoreAllMocks() })
const response = data => ({ ok: true, json: async () => data })
const base = { enabled: false, configured: true, key_ready: true, backend_configured: true, message: '已配置未启用', last_result: '尚未测试', cookie_mask: '••••', quote_status: 'quote_pending', quote_message: '报价页动态加载待核定' }

test.each([
  ['pending_credentials', '凭据未配置'],
  ['password_js_required', '官网需 JS，账密模式不可用'],
  ['cookie_valid', 'Cookie 有效'],
  ['cookie_invalid', 'Cookie 已失效'],
  ['cookie_unconfirmed', 'Cookie 未确认'],
  ['access_blocked', '触发验证码或风控已停手'],
])('管理员区分别显示 %s 和独立报价状态', async (auth_status, auth_message) => {
  const state = { ...base, auth_status, auth_message, verified_at: auth_status === 'cookie_valid' ? '2026-11-13T10:00:00+08:00' : null, auth_checked_at: '2026-11-13T10:00:00+08:00' }
  vi.stubGlobal('fetch', vi.fn(async () => response(state)))
  wrapper = mount(Sci99Settings); await flushPromises()
  expect(wrapper.get('[data-testid=sci99-auth-state]').text()).toContain(auth_message)
  expect(wrapper.get('[data-testid=sci99-quote-state]').text()).toContain('报价页动态加载待核定')
  expect(wrapper.get('[data-testid=sci99-auth-state]').text()).toContain(auth_status === 'cookie_valid' ? '验证时间' : '检查时间')
  expect(wrapper.get('input[type=checkbox]').element.checked).toBe(false)
  expect(wrapper.get('textarea').element.value).toBe('')
})

test('测试后同步 Cookie 验证时间且不把登录有效显示为抓价成功', async () => {
  let tested = false
  vi.stubGlobal('fetch', vi.fn(async (path, options = {}) => {
    if (options.method === 'POST') { tested = true; return response({ status: 'cookie_valid', message: 'Cookie 有效，未抓取报价' }) }
    return response({ ...base, auth_status: tested ? 'cookie_valid' : 'cookie_unconfirmed', auth_message: tested ? 'Cookie 有效，未抓取报价' : 'Cookie 未确认', verified_at: tested ? '2026-11-13T10:00:00+08:00' : null })
  }))
  wrapper = mount(Sci99Settings); await flushPromises()
  await wrapper.findAll('button').find(x => x.text() === '测试连接').trigger('click'); await flushPromises()
  expect(wrapper.get('[data-testid=sci99-auth-state]').text()).toContain('验证时间')
  expect(wrapper.get('[data-testid=sci99-quote-state]').text()).toContain('报价页动态加载待核定')
  expect(wrapper.text()).not.toContain('报价抓取成功')
  expect(wrapper.emitted('saved')).toHaveLength(1)
})
