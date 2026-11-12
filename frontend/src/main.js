import { createApp } from 'vue'
import { createRouter, createWebHashHistory } from 'vue-router'
import App from './App.vue'
import MarketView from './views/MarketView.vue'
import './style.css'

const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/', component: MarketView },
    { path: '/commodity/:code', component: () => import('./views/DetailView.vue') },
    { path: '/alerts', component: () => import('./views/AlertsView.vue') },
    { path: '/push-logs', component: () => import('./views/PushLogsView.vue') },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
  scrollBehavior: () => ({ top: 0 }),
})

createApp(App).use(router).mount('#app')
