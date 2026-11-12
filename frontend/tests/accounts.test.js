import { afterEach, expect, test, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { user } from '../src/auth'
import { guard } from '../src/router'
import App from '../src/App.vue'
import AuthView from '../src/views/AuthView.vue'
import PushSettingsView from '../src/views/PushSettingsView.vue'
import UsersView from '../src/views/UsersView.vue'

let wrapper
afterEach(() => { wrapper?.unmount(); user.value = null; vi.unstubAllGlobals(); vi.restoreAllMocks() })
const pending = { status: 'pending_configuration', message: 'email：待配置：RESEND_API_KEY' }
function mockFetch(handler) {
  const fn = vi.fn(async (path, options = {}) => {
    const result = await handler(path, options)
    return result?.ok === false ? result : { ok: true, json: async () => result }
  })
  vi.stubGlobal('fetch', fn)
  return fn
}
function button(text) { return wrapper.findAll('button').find(b => b.text() === text) }
function render(component) { return mount(component, { global: { stubs: { RouterLink: { template: '<a><slot /></a>' } } } }) }

test('普通账号导航隐藏管理页面，读取行情不请求全局数据源接口', async () => {
  user.value = { id: 2, username: 'account_a', role: 'user' }
  const fetch = mockFetch(() => ({ items: [] }))
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: { template: '<p>行情</p>' } }] })
  await router.push('/'); await router.isReady()
  wrapper = mount(App, { global: { plugins: [router] } }); await flushPromises()
  expect(wrapper.text()).toContain('我的提醒')
  expect(wrapper.text()).toContain('我的推送设置')
  expect(wrapper.text()).not.toContain('全局设置')
  expect(wrapper.text()).not.toContain('用户管理')
  expect(wrapper.text()).not.toContain('数据源状态')
  expect(fetch.mock.calls.map(c => c[0])).not.toContain('/api/sources/status')
})

test('管理员导航显示全局设置和用户管理', async () => {
  user.value = { id: 1, username: 'admin', role: 'admin' }
  mockFetch(() => ({ items: [] }))
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: { template: '<p>行情</p>' } }] })
  await router.push('/'); await router.isReady()
  wrapper = mount(App, { global: { plugins: [router] } }); await flushPromises()
  expect(wrapper.text()).toContain('全局设置')
  expect(wrapper.text()).toContain('用户管理')
})

test('路由拦截匿名与普通账号手动输入管理地址', async () => {
  mockFetch(() => ({ ok: false, status: 401 }))
  expect(await guard({ meta: {} })).toBe('/login')
  mockFetch(() => ({ role: 'user' }))
  expect(await guard({ meta: { admin: true } })).toBe('/')
  mockFetch(() => ({ role: 'admin' }))
  expect(await guard({ meta: { admin: true } })).toBe(true)
  expect(await guard({ meta: { public: true } })).toBe(true)
})

test('个人邮箱保存与待配置展示不混入全局 Telegram 设置', async () => {
  const requests = mockFetch((path, options) => ({ email: options.body ? JSON.parse(options.body).email : 'a@example.com', channel: pending }))
  wrapper = render(PushSettingsView); await flushPromises()
  expect(wrapper.get('input').element.value).toBe('a@example.com')
  await wrapper.get('input').setValue('next@example.com')
  await wrapper.get('form').trigger('submit'); await flushPromises()
  expect(JSON.parse(requests.mock.calls.at(-1)[1].body)).toEqual({ email: 'next@example.com' })
  expect(wrapper.get('[role=status]').text()).toContain('已保存')
  expect(wrapper.text()).toContain('待配置')
})

test('用户管理切换注册、禁用、启用和确认删除', async () => {
  let enabled = true, registration = true, removed = false
  mockFetch((path, options) => {
    if (path === '/api/admin/registration') registration = JSON.parse(options.body).enabled
    if (path === '/api/admin/users/2' && options.method === 'PUT') enabled = JSON.parse(options.body).enabled
    if (options.method === 'DELETE') removed = true
    return { items: [{ id: 1, username: 'admin', role: 'admin', enabled: true }, ...removed ? [] : [{ id: 2, username: 'account_a', role: 'user', enabled }]], registration_enabled: registration }
  })
  wrapper = render(UsersView); await flushPromises()
  await button('关闭注册').trigger('click'); await flushPromises()
  expect(wrapper.text()).toContain('开放注册')
  await button('禁用').trigger('click'); await flushPromises()
  expect(wrapper.text()).toContain('已禁用')
  await button('启用').trigger('click'); await flushPromises()
  await button('删除').trigger('click'); await flushPromises()
  expect(removed).toBe(false)
  await button('确认删除').trigger('click'); await flushPromises()
  expect(wrapper.findAll('tbody tr')).toHaveLength(1)
})

test('登录与注册提交失败时显示服务端原因，提交不夹带角色', async () => {
  const requests = mockFetch(() => ({ ok: false, status: 403, json: async () => ({ detail: '注册已关闭' }) }))
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/register', component: AuthView }] })
  await router.push('/register'); await router.isReady()
  wrapper = mount(AuthView, { global: { plugins: [router] } })
  await wrapper.findAll('input')[0].setValue('account_a')
  await wrapper.findAll('input')[1].setValue('isolated-password')
  await wrapper.get('form').trigger('submit'); await flushPromises()
  expect(requests.mock.calls[0][0]).toBe('/api/auth/register')
  expect(JSON.parse(requests.mock.calls[0][1].body)).toEqual({ username: 'account_a', password: 'isolated-password' })
  expect(wrapper.get('[role=alert]').text()).toContain('注册已关闭')
  expect(wrapper.get('button').attributes('disabled')).toBeUndefined()
})

test('注册通用响应只引导登录，不自动探测登录或创建会话', async () => {
  const message = '注册请求已处理。请使用所填账号和密码登录；无法登录时请更换账号名或联系管理员。'
  const requests = mockFetch(() => ({ message }))
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/register', component: AuthView }] })
  await router.push('/register'); await router.isReady()
  wrapper = mount(AuthView, { global: { plugins: [router] } })
  await wrapper.findAll('input')[0].setValue('chosen_name')
  await wrapper.findAll('input')[1].setValue('isolated-password')
  await wrapper.get('form').trigger('submit'); await flushPromises()
  expect(requests.mock.calls.map(call => call[0])).toEqual(['/api/auth/register'])
  expect(wrapper.get('[role=status]').text()).toBe(message)
  expect(wrapper.findAll('input')[1].element.value).toBe('')
  expect(wrapper.find('a').text()).toContain('去登录')
})
