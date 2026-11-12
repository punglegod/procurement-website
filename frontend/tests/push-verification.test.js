import { afterEach, expect, test, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import PushSettingsView from '../src/views/PushSettingsView.vue'
import PushLogsView from '../src/views/PushLogsView.vue'

let wrapper
afterEach(() => { wrapper?.unmount(); vi.unstubAllGlobals(); vi.restoreAllMocks() })
const channel = { status: 'pending_verification', message: '邮箱未验证；管理员 Telegram 不受影响' }
const settings = (email = 'owner@example.com', verified = false) => ({ user_id: 2, email, email_verified: verified, channel })
const ok = data => ({ ok: true, json: async () => data })
const fail = (status, detail) => ({ ok: false, status, json: async () => ({ detail }) })
function render(component) { return mount(component, { global: { stubs: { RouterLink: { template: '<a><slot /></a>' } } } }) }
function button(text) { return wrapper.findAll('button').find(item => item.text() === text) }

test('保存邮箱不自动发送，主动请求验证码并确认后显示已验证；换邮箱撤销验证', async () => {
  let current = settings(), requested = 0, confirmed = 0
  const fetch = vi.fn(async (path, options = {}) => {
    if (path.endsWith('/request')) { requested++; return ok({ status: 'sent' }) }
    if (path.endsWith('/confirm')) {
      expect(JSON.parse(options.body)).toEqual({ code: '12345678' })
      confirmed++; current.email_verified = true; return ok({ ...current })
    }
    if (options.method === 'PUT') current = settings(JSON.parse(options.body).email)
    return ok({ ...current })
  })
  vi.stubGlobal('fetch', fetch)
  wrapper = render(PushSettingsView); await flushPromises()
  expect(wrapper.get('[data-testid=email-verification-status]').text()).toContain('未验证')
  await wrapper.get('input[type=email]').setValue('next@example.com')
  expect(button('发送验证码').attributes('disabled')).toBeDefined()
  await wrapper.get('form').trigger('submit'); await flushPromises()
  expect(requested).toBe(0)
  await button('发送验证码').trigger('click'); await flushPromises()
  expect(requested).toBe(1)
  expect(wrapper.get('[role=status]').text()).toContain('验证码已发送至 next@example.com')
  await wrapper.get('input[autocomplete=one-time-code]').setValue('12345678')
  await wrapper.findAll('form')[1].trigger('submit'); await flushPromises()
  expect(confirmed).toBe(1)
  expect(wrapper.get('[data-testid=email-verification-status]').text()).toContain('已验证')
  expect(button('发送验证码')).toBeUndefined()
  await wrapper.get('input[type=email]').setValue('changed@example.com')
  await wrapper.get('form').trigger('submit'); await flushPromises()
  expect(wrapper.get('[data-testid=email-verification-status]').text()).toContain('未验证')
  expect(wrapper.get('input[autocomplete=one-time-code]').element.value).toBe('')
  expect(requested).toBe(1)
})

test('保存成功使用已提交快照，省略或错误回显邮箱的验证状态不能移用', async () => {
  let finishSave, submitted
  vi.stubGlobal('fetch', vi.fn(async (path, options = {}) => {
    if (options.method !== 'PUT') return ok(settings('owner@example.com', true))
    submitted = JSON.parse(options.body).email
    return new Promise(resolve => { finishSave = data => resolve(ok(data)) })
  }))
  wrapper = render(PushSettingsView); await flushPromises()
  const acknowledgements = [
    { email_verified: true, channel },
    { email: 'another@example.com', email_verified: true, channel },
  ]
  for (const [index, acknowledgement] of acknowledgements.entries()) {
    const address = `next${index}@example.com`
    await wrapper.get('input[type=email]').setValue(`  ${address}  `)
    await wrapper.get('form').trigger('submit'); await flushPromises()
    expect(submitted).toBe(address)
    // 模拟保存过程中发生的脚本输入，响应仍应确认请求发出时的快照。
    const input = wrapper.get('input[type=email]').element
    input.value = 'unsubmitted@example.com'
    input.dispatchEvent(new Event('input', { bubbles: true }))
    finishSave(acknowledgement); await flushPromises()
    expect(wrapper.get('input[type=email]').element.value).toBe(address)
    expect(wrapper.get('[data-testid=email-verification-status]').text()).toBe('已保存邮箱：未验证')
    expect(button('发送验证码').attributes('disabled')).toBeUndefined()
    expect(wrapper.get('[role=status]').text()).toContain('个人邮箱已保存，请验证')
  }
})

test('待配置和验证码限流均明确提示，不伪报发送成功', async () => {
  let mode = 'pending'
  vi.stubGlobal('fetch', vi.fn(async path => {
    if (!path.endsWith('/request')) return ok(settings())
    return mode === 'pending' ? ok({ status: 'pending_configuration' }) : fail(429, '验证码发送过于频繁，请稍后重试。')
  }))
  wrapper = render(PushSettingsView); await flushPromises()
  await button('发送验证码').trigger('click'); await flushPromises()
  expect(wrapper.get('[role=status]').text()).toContain('邮件服务待配置')
  expect(wrapper.text()).not.toContain('验证码已发送至')
  mode = 'limited'
  await button('发送验证码').trigger('click'); await flushPromises()
  expect(wrapper.get('[role=alert]').text()).toContain('发送过于频繁')
  expect(button('发送验证码').attributes('disabled')).toBeUndefined()
})

test('验证码失败和送达未知使用服务端message如实显示', async () => {
  let result = { status: 'unknown', message: '验证码送达未知，请先检查收件箱，不要立即重复发送。' }
  vi.stubGlobal('fetch', vi.fn(async path => ok(path.endsWith('/request') ? result : settings())))
  wrapper = render(PushSettingsView); await flushPromises()
  await button('发送验证码').trigger('click'); await flushPromises()
  expect(wrapper.get('[role=alert]').text()).toContain('验证码送达未知')
  expect(wrapper.text()).not.toContain('验证码已发送至')
  result = { status: 'failed', message: '邮件服务未接受验证码，请稍后重试。' }
  await button('发送验证码').trigger('click'); await flushPromises()
  expect(wrapper.get('[role=alert]').text()).toContain('邮件服务未接受验证码')
})

test('邮箱验证日志与待验证状态使用明确中文标签', async () => {
  vi.stubGlobal('fetch', vi.fn(async path => ok(path.includes('status') ? channel : {
    total: 1, items: [{ id: 9, kind: 'verification', rule_id: null, code: null, channel: 'email', status: 'pending_verification', message: '邮箱验证请求', detail: '等待邮箱归属验证', created_at: '2026-11-12T09:00:00+08:00' }],
  })))
  wrapper = render(PushLogsView); await flushPromises()
  expect(wrapper.get('tbody').text()).toContain('邮箱验证')
  expect(wrapper.get('tbody').text()).toContain('待验证邮箱')
  expect(wrapper.get('tbody').text()).not.toContain('规则 #null')
  expect(wrapper.get('tbody').text()).not.toContain('pending_verification')
})

test('验证码长度校验与服务端无效码错误后仍允许更正', async () => {
  let confirmRequests = 0
  vi.stubGlobal('fetch', vi.fn(async path => {
    if (path.endsWith('/confirm')) { confirmRequests++; return fail(400, '验证码无效或已过期。') }
    return ok(settings())
  }))
  wrapper = render(PushSettingsView); await flushPromises()
  await wrapper.get('input[autocomplete=one-time-code]').setValue('1234')
  await wrapper.findAll('form')[1].trigger('submit'); await flushPromises()
  expect(confirmRequests).toBe(0)
  expect(wrapper.get('[role=alert]').text()).toContain('8 位数字')
  await wrapper.get('input[autocomplete=one-time-code]').setValue('12345678')
  await wrapper.findAll('form')[1].trigger('submit'); await flushPromises()
  expect(confirmRequests).toBe(1)
  expect(wrapper.get('[role=alert]').text()).toContain('无效或已过期')
  expect(button('确认验证码').attributes('disabled')).toBeUndefined()
  expect(wrapper.get('[data-testid=email-verification-status]').text()).toContain('未验证')
})

test('初始读取失败禁止保存，重试后恢复编辑', async () => {
  let available = false
  const fetch = vi.fn(async () => available ? ok(settings()) : fail(503, '暂不可用'))
  vi.stubGlobal('fetch', fetch)
  wrapper = render(PushSettingsView); await flushPromises()
  expect(button('保存邮箱').attributes('disabled')).toBeDefined()
  await wrapper.get('form').trigger('submit'); await flushPromises()
  expect(fetch).toHaveBeenCalledTimes(1)
  available = true
  await button('重新读取').trigger('click'); await flushPromises()
  expect(button('保存邮箱').attributes('disabled')).toBeUndefined()
  expect(wrapper.get('input[type=email]').element.value).toBe('owner@example.com')
})

test('未验证测试邮件403有可读错误，管理员Telegram成功仍可展示', async () => {
  let admin = false
  vi.stubGlobal('fetch', vi.fn(async (path, options = {}) => {
    if (options.method === 'POST') return admin ? ok({ results: [{ channel: 'telegram', status: 'sent', detail: 'Telegram 已接受' }] }) : fail(403, '请先验证收件邮箱。')
    return ok(path.includes('status') ? channel : { total: 0, items: [] })
  }))
  wrapper = render(PushLogsView); await flushPromises()
  await button('发送测试推送').trigger('click'); await flushPromises()
  expect(wrapper.get('[role=alert]').text()).toContain('请先验证收件邮箱')
  expect(wrapper.text()).toContain('我的推送设置')
  admin = true
  await button('发送测试推送').trigger('click'); await flushPromises()
  expect(wrapper.get('[role=status]').text()).toContain('telegram 发送成功')
})

test('日志翻页成功后页码与记录同步提交，手动刷新保持当前页', async () => {
  let finishPage
  const requests = []
  vi.stubGlobal('fetch', vi.fn(async path => {
    requests.push(path)
    if (path.includes('status')) return ok(channel)
    const data = page => ({ total: 31, items: [{ id: page, kind: 'test', channel: 'email', status: 'sent', message: `PAGE_${page}`, created_at: '2026-11-12T09:00:00+08:00' }] })
    if (path.endsWith('page=2') && !finishPage) return new Promise(resolve => { finishPage = () => resolve(ok(data(2))) })
    return ok(data(path.endsWith('page=2') ? 2 : 1))
  }))
  wrapper = render(PushLogsView); await flushPromises()
  await button('下一页').trigger('click'); await flushPromises()
  expect(wrapper.get('.table-footer').text()).toContain('第 1 页')
  expect(wrapper.get('tbody').text()).toContain('PAGE_1')
  finishPage(); await flushPromises()
  expect(wrapper.get('.table-footer').text()).toContain('第 2 页')
  expect(wrapper.get('tbody').text()).toContain('PAGE_2')
  await button('刷新日志').trigger('click'); await flushPromises()
  expect(requests.filter(path => path.includes('logs')).at(-1)).toBe('/api/alerts/logs?page=2')
})
