import { afterEach, expect, test, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import PushSettingsView from '../src/views/PushSettingsView.vue'
import PushLogsView from '../src/views/PushLogsView.vue'
import SettingsView from '../src/views/SettingsView.vue'
import Sci99Settings from '../src/components/Sci99Settings.vue'
import { useMarket } from '../src/store'

let wrapper
afterEach(() => { wrapper?.unmount(); vi.unstubAllGlobals(); vi.restoreAllMocks() })
const ok = data => ({ ok:true, json:async()=>data })
const render = component => mount(component,{global:{stubs:{RouterLink:{template:'<a><slot /></a>'}}}})
const button = text => wrapper.findAll('button').find(item=>item.text()===text)
const state = {enabled:false,configured:false,key_ready:true,backend_configured:false,message:'未配置',last_result:'尚未测试'}

test('日报开关只保存本人设置，不发测试消息，不改邮箱验证', async () => {
  let current={user_id:2,email:'owner@example.invalid',email_verified:true,daily_digest_enabled:false,channel:{message:'已配置'}}
  const calls=[]
  vi.stubGlobal('fetch',vi.fn(async (path,options={})=>{
    calls.push([path,options])
    if(options.method==='PUT') current={...current,...JSON.parse(options.body)}
    return ok(current)
  }))
  wrapper=render(PushSettingsView); await flushPromises()
  await wrapper.get('input[type=checkbox]').setValue(true)
  await button('保存日报设置').trigger('click'); await flushPromises()
  const request=JSON.parse(calls.at(-1)[1].body)
  expect(request).toEqual({email:'owner@example.invalid',daily_digest_enabled:true})
  expect(wrapper.get('[data-testid=email-verification-status]').text()).toContain('已验证')
  expect(calls.every(([path])=>path==='/api/me/push-settings')).toBe(true)
  expect(wrapper.get('[role=status]').text()).toContain('日报设置已保存')
})

test('卓创保存只提交新凭据，成功清空输入并保留掩码状态', async () => {
  let submitted
  vi.stubGlobal('fetch',vi.fn(async (path,options={})=>{
    if(options.method==='PUT') { submitted=JSON.parse(options.body); return ok({...state,enabled:true,configured:true,backend_configured:true,message:'已启用',username_mask:'mo••••er',password_mask:'••••'}) }
    return ok(state)
  }))
  wrapper=render(Sci99Settings); await flushPromises()
  await wrapper.get('input[type=checkbox]').setValue(true)
  await wrapper.get('input[autocomplete=off]').setValue('mock-user')
  await wrapper.get('input[type=password]').setValue('mock-password')
  expect(button('测试连接').attributes('disabled')).toBeDefined()
  await wrapper.get('form').trigger('submit'); await flushPromises()
  expect(Object.keys(submitted).sort()).toEqual(['enabled','password','username'])
  expect(wrapper.get('input[autocomplete=off]').element.value).toBe('')
  expect(wrapper.get('input[type=password]').element.value).toBe('')
  expect(wrapper.get('textarea').element.value).toBe('')
  expect(wrapper.text()).toContain('mo••••er')
  expect(wrapper.emitted('saved')).toHaveLength(1)
})

test('缺加密密钥禁止输入凭据，未配置测试如实反馈，不伪报成功', async () => {
  let tested=0
  vi.stubGlobal('fetch',vi.fn(async (path,options={})=>{
    if(options.method==='POST') { tested++; return ok({status:'pending_credentials',message:'未配置，未执行网络请求'}) }
    return ok({...state,key_ready:false})
  }))
  wrapper=render(Sci99Settings); await flushPromises()
  expect(wrapper.get('input[type=password]').attributes('disabled')).toBeDefined()
  expect(wrapper.text()).toContain('SETTINGS_ENCRYPTION_KEY')
  await button('测试连接').trigger('click'); await flushPromises()
  expect(tested).toBe(1)
  expect(wrapper.get('[role=status]').text()).toContain('未配置')
})

test('全局设置配置保存后同步数据源与侧栏状态，保留四轮及进度开关', async () => {
  let enabled=false,progress=true
  vi.stubGlobal('fetch',vi.fn(async (path,options={})=>{
    if(path==='/api/admin/sources/sci99') {
      if(options.method==='PUT') enabled=JSON.parse(options.body).enabled
      return ok({...state,enabled,configured:true,message:enabled?'已启用':'已配置未启用'})
    }
    if(path==='/api/admin/fetch-settings') { if(options.method==='PUT') progress=JSON.parse(options.body).progress_enabled; return ok({progress_enabled:progress}) }
    if(path==='/api/sources/status') return ok({items:[{source:'sci99',name:'卓创资讯',message:enabled?'已启用':'已配置未启用'}]})
    if(path==='/api/admin/fetch-runs') return ok({items:[]})
    return ok({message:'待配置'})
  }))
  wrapper=render(SettingsView); await flushPromises()
  expect(wrapper.text()).toContain('08:00、08:30、09:00、15:30')
  const sci=wrapper.getComponent(Sci99Settings)
  await sci.get('input[type=checkbox]').setValue(true)
  await sci.get('form').trigger('submit'); await flushPromises()
  expect(useMarket().sources.value[0].message).toBe('已启用')
  expect(wrapper.text()).toContain('抓取进度推送（仅管理员 Telegram）')
})

test('推送记录分别显示日报、抓取进度，不误标为规则', async () => {
  const logs=['digest','fetch_progress'].map((kind,index)=>({id:index+1,kind,status:'sent',created_at:'2026-11-13T09:03:00+08:00',message:'隔离内容',detail:'已接受',channel:'telegram'}))
  vi.stubGlobal('fetch',vi.fn(async path=>ok(path==='/api/alerts/status'?{message:'已配置'}:{items:logs,total:2})))
  wrapper=render(PushLogsView); await flushPromises()
  expect(wrapper.get('tbody').text()).toContain('日报')
  expect(wrapper.get('tbody').text()).toContain('抓取进度')
  expect(wrapper.get('tbody').text()).not.toContain('规则 #null')
})
