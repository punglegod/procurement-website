import { createApp } from 'vue'
import App from './App.vue'
import { router } from './router'
import './style.css'

if (['/login', '/register'].includes(window.location.pathname) && !window.location.hash) window.location.hash = window.location.pathname

createApp(App).use(router).mount('#app')
