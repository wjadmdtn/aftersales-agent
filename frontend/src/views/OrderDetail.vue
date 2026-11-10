<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, STATUS_LABEL, resultMessage } from '@/api/rest'
import type { OrderData } from '@/types'

const props = defineProps<{ orderId?: string }>()
const emit = defineEmits<{ (e: 'contact', orderId: string): void }>()

const route = useRoute()
const router = useRouter()

// 路由页用 params，单测直接传 props
const orderId = computed(() => props.orderId ?? String(route.params.orderId ?? ''))
const order = ref<OrderData | null>(null)
const error = ref<string | null>(null)
const logistics = ref<{ current_node: string; tracks: { node: string; at: string }[] } | null>(null)
const noLogistics = ref(false)
const loading = ref(true)

async function load() {
  if (!orderId.value) return
  loading.value = true
  error.value = null
  noLogistics.value = false
  logistics.value = null
  const res = await api.getOrder(orderId.value)
  if (res.code !== 1000 || !res.data) {
    error.value = resultMessage(res.code)
    loading.value = false
    return
  }
  order.value = res.data

  const lg = await api.getLogistics(orderId.value)
  if (lg.code === 1000 && lg.data) {
    logistics.value = lg.data
  } else {
    noLogistics.value = true
  }
  loading.value = false
}

onMounted(load)
watch(orderId, load)

function fmtTime(iso: string): string {
  return new Date(iso).toLocaleString('zh-CN')
}

function onContact(id: string) {
  emit('contact', id)
  if (props.orderId) return // 单测场景由父级处理
  router.push({ name: 'chat', query: { order_id: id } })
}
</script>

<template>
  <div class="order-page" data-test="order-detail">
    <div v-if="loading" class="state">加载中…</div>

    <div v-else-if="error" class="state state--error" data-test="order-error">
      {{ error }}
    </div>

    <template v-else-if="order">
      <header>
        <h2>订单 {{ order.order_id }}</h2>
        <el-tag :type="order.status === 'REFUNDED' ? 'info' : 'success'">
          {{ STATUS_LABEL[order.status] ?? order.status }}
        </el-tag>
      </header>

      <section class="card">
        <div class="row">
          <span class="label">金额</span>
          <span class="amount">¥{{ order.amount }}</span>
        </div>
        <div class="row">
          <span class="label">下单时间</span>
          <span>{{ fmtTime(order.created_at) }}</span>
        </div>
        <div class="row" v-if="order.product_type">
          <span class="label">品类</span>
          <span>{{ order.product_type }}</span>
        </div>
      </section>

      <section class="card">
        <h3>收货地址</h3>
        <p>{{ order.address.receiver }} {{ order.address.phone }}</p>
        <p>
          {{ order.address.province }}{{ order.address.city }}{{ order.address.district
          }}{{ order.address.detail }}
        </p>
      </section>

      <section class="card">
        <h3>物流</h3>
        <p v-if="noLogistics" class="muted">暂无物流信息</p>
        <template v-else-if="logistics">
          <p>{{ logistics.current_node }}</p>
          <ul class="tracks">
            <li v-for="(t, i) in logistics.tracks" :key="i">
              {{ t.node }} · {{ fmtTime(t.at) }}
            </li>
          </ul>
        </template>
      </section>

      <footer>
        <button class="contact" data-test="contact-agent" @click="onContact(order.order_id)">
          <span class="icon">💬</span>
          <span>联系客服</span>
        </button>
      </footer>
    </template>
  </div>
</template>

<style scoped>
.order-page {
  max-width: 720px;
  margin: 0 auto;
  padding: 24px 24px 96px;
  min-height: 100%;
  box-sizing: border-box;
}
header {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 16px;
}
header h2 {
  margin: 0;
  font-size: 20px;
}
.card {
  border: 1px solid #ebeef5;
  border-radius: 8px;
  padding: 16px;
  margin-bottom: 16px;
}
.card h3 {
  margin: 0 0 8px;
  font-size: 14px;
  color: #606266;
}
.card p {
  margin: 4px 0;
}
.row {
  display: flex;
  justify-content: space-between;
  padding: 6px 0;
}
.label {
  color: #909399;
}
.amount {
  font-size: 20px;
  color: #f56c6c;
  font-weight: 600;
}
.muted {
  color: #909399;
}
.tracks {
  margin: 8px 0 0;
  padding-left: 18px;
  color: #606266;
  font-size: 13px;
}
.state {
  padding: 48px;
  text-align: center;
  color: #909399;
}
.state--error {
  color: #f56c6c;
}
footer {
  position: fixed;
  left: 0;
  right: 0;
  bottom: 0;
  padding: 12px 24px;
  background: #fff;
  border-top: 1px solid #ebeef5;
  display: flex;
  justify-content: center;
}
.contact {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 10px 28px;
  border: none;
  border-radius: 999px;
  background: #409eff;
  color: #fff;
  font-size: 15px;
  cursor: pointer;
}
.icon {
  font-size: 18px;
}
</style>
