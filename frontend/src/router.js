import { createRouter, createWebHashHistory } from 'vue-router'
import { loadUser } from './auth'
import MarketView from './views/MarketView.vue'
export const routes = [
  { path: '/', component: MarketView },
  { path: '/commodity/:code', component: () => import('./views/DetailView.vue') },
  { path: '/alerts', component: () => import('./views/AlertsView.vue') },
  { path: '/push-logs', component: () => import('./views/PushLogsView.vue') },
  { path: '/push-settings', component: () => import('./views/PushSettingsView.vue') },
  { path: '/account', component: () => import('./views/AccountView.vue') },
  { path: '/settings', component: () => import('./views/SettingsView.vue'), meta: { admin: true } },
  { path: '/users', component: () => import('./views/UsersView.vue'), meta: { admin: true } },
  { path: '/login', component: () => import('./views/AuthView.vue'), meta: { public: true } },
  { path: '/register', component: () => import('./views/AuthView.vue'), meta: { public: true } },
  { path: '/:pathMatch(.*)*', redirect: '/' },
]
export async function guard(to) {
  if (to.meta.public) return true
  const account = await loadUser()
  if (!account) return '/login'
  if (to.meta.admin && account.role !== 'admin') return '/'
  return true
}
export const router = createRouter({ history: createWebHashHistory(), routes, scrollBehavior: () => ({ top: 0 }) })
router.beforeEach(guard)
