<script setup>
import { watch } from 'vue'
import { useRoute } from 'vue-router'
import { user, logout } from './auth'
import { useMarket } from './store'
import { time } from './api'

const { sources, loading, error, refreshedAt, refresh } = useMarket()
const route = useRoute()
watch(user, value => { if (value) refresh() }, { immediate: true })
</script>

<template>
  <RouterView v-if="route.meta.public" />
  <div v-else-if="user" class="app-shell">
    <aside class="sidebar">
      <RouterLink to="/" class="brand" aria-label="采购行情首页">
        <span class="brand-mark" aria-hidden="true"><i></i><i></i><i></i></span>
        <span>采购行情</span>
      </RouterLink>
      <div class="nav-label">工作台</div>
      <RouterLink to="/" class="nav-item" :class="{ active: $route.path === '/' }"><span aria-hidden="true">▦</span> 行情总览 <span class="nav-arrow">↗</span></RouterLink>
      <div v-if="$route.path.startsWith('/commodity/')" class="nav-item active"><span aria-hidden="true">⌁</span> 品种走势</div>
      <RouterLink to="/alerts" class="nav-item" :class="{ active: $route.path === '/alerts' }"><span aria-hidden="true">♧</span> 我的提醒</RouterLink>
      <RouterLink to="/push-logs" class="nav-item" :class="{ active: $route.path === '/push-logs' }"><span aria-hidden="true">≡</span> 推送日志</RouterLink>
      <RouterLink to="/push-settings" class="nav-item">我的推送设置</RouterLink>
      <RouterLink to="/account" class="nav-item">账号与密码</RouterLink>
      <template v-if="user.role === 'admin'">
        <div class="nav-label">站点管理</div>
        <RouterLink to="/settings" class="nav-item">全局设置</RouterLink>
        <RouterLink to="/users" class="nav-item">用户管理</RouterLink>
      </template>
      <section v-if="user.role === 'admin'" class="source-panel" aria-label="数据源状态">
        <div class="nav-label">数据源状态</div>
        <div v-for="source in sources" :key="source.source" class="source-item">
          <span class="small-dot" :class="source.status === 'ok' ? 'dot-green' : 'dot-muted'"></span>
          <div><strong>{{ source.source === 'free' ? '世铝网 / 南海灵通' : source.name }}</strong><small>{{ source.source === 'sci99' && source.status === 'pending_credentials' ? '暂缓启用 · 待配置凭据' : source.message }}</small></div>
        </div>
        <p v-if="!sources.length" class="muted">{{ loading ? '正在读取状态…' : '暂未获取来源状态' }}</p>
      </section>
      <div class="sidebar-bottom">{{ user.username }} · {{ user.role === 'admin' ? '管理员' : '普通账号' }} <button class="text-button" @click="logout">退出</button></div>
    </aside>
    <div class="workspace">
      <main>
        <div v-if="error" class="notice error-notice" role="alert">{{ error }}<span v-if="refreshedAt">当前保留上次读取的数据（{{ time(refreshedAt) }}）。</span><button class="text-button" :disabled="loading" @click="refresh">重新读取</button></div>
        <RouterView />
      </main>
      <footer class="page-footer"><span>价格随报价日期展示；周末及节假日仍会提示旧报价。</span></footer>
    </div>
  </div>
</template>
