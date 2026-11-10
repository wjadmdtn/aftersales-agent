import { createRouter, createWebHashHistory } from 'vue-router'

export const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/', redirect: '/order/ORD1002' },
    { path: '/order/:orderId', name: 'order', component: () => import('./views/OrderDetail.vue') },
    { path: '/chat', name: 'chat', component: () => import('./views/UserChat.vue') },
    { path: '/console', name: 'console', component: () => import('./views/AgentConsole.vue') },
  ],
})
