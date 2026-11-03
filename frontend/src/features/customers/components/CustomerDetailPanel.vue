<script setup lang="ts">
// 客户页面传入 ID，详情模块独立加载事实汇总、订单和可编辑跟进资料。
import { toRef } from 'vue'
import { useCustomerDetail } from '../composables/useCustomerDetail'
import { dateTime, money } from '../../../shared/commerce'
import AppPagination from '../../../shared/components/AppPagination.vue'
const props = defineProps<{ customerId: number | null }>()
const {
  data,
  page,
  loading,
  saving,
  error,
  profileError,
  saved,
  tagsDraft,
  noteDraft,
  revision,
  pageCount,
  load,
  saveProfile,
  reloadProfile,
  changePage,
} = useCustomerDetail(toRef(props, 'customerId'))
const statuses: Record<string, string> = {
  pending_payment: '待付款',
  paid_pending_shipment: '待发货',
  in_delivery: '配送中',
  completed: '已完成',
  cancelled: '已取消',
  refunded: '历史已退款',
}
</script>

<template>
  <section class="customer-detail-panel" aria-label="客户经营与跟进详情">
    <p v-if="error" class="ops-error" role="alert">
      {{ error }} <button class="text-action" @click="load">重试</button>
    </p>
    <p v-if="loading" class="module-empty">正在加载客户详情…</p>
    <template v-if="data">
      <div class="address-list-heading">
        <h2>{{ data.customer.name }} · 客户详情</h2>
        <a class="ops-button primary" :href="`/orders?create_customer_id=${customerId}`"
          >为该客户创建订单</a
        >
      </div>
      <div class="customer-summary">
        <div>
          <small>订单 / 已确认收款订单</small
          ><strong>{{ data.summary.order_count }} / {{ data.summary.paid_order_count }}</strong>
        </div>
        <div>
          <small>累计确认收款</small><strong>{{ money(data.summary.confirmed_amount) }}</strong>
        </div>
        <div>
          <small>累计实际退款</small><strong>{{ money(data.summary.refunded_amount) }}</strong>
        </div>
        <div>
          <small>净确认收款</small><strong>{{ money(data.summary.net_confirmed_amount) }}</strong>
        </div>
      </div>
      <p class="ops-subtle">
        最近确认收款：{{
          dateTime(data.summary.last_purchase_at)
        }}；金额来自登记事实，净收款不等于利润。
      </p>
      <form class="customer-followup" @submit.prevent="saveProfile">
        <h3>客户标签与跟进备注</h3>
        <label
          >标签（逗号分隔，最多 20 个）<input
            v-model="tagsDraft"
            :disabled="saving || revision === null"
            maxlength="620"
            placeholder="例如：老客户，关注新品"
        /></label>
        <label
          >跟进备注<textarea
            v-model="noteDraft"
            :disabled="saving || revision === null"
            maxlength="5000"
            rows="3"
            placeholder="记录沟通情况、需求和后续跟进事项"
          ></textarea>
        </label>
        <p v-if="profileError" class="ops-error" role="alert">{{ profileError }}</p>
        <p v-if="saved" role="status">跟进资料已保存</p>
        <div class="section-actions">
          <button
            class="ops-button"
            type="button"
            :disabled="saving || loading"
            @click="reloadProfile"
          >
            重新加载跟进资料</button
          ><button class="ops-button primary" :disabled="saving || revision === null">
            {{ saving ? '保存中…' : '保存跟进资料' }}
          </button>
        </div>
      </form>
      <div v-if="data.common_products.length" class="customer-common-products">
        <h3>已收款订单中的常购商品</h3>
        <p class="ops-subtle">按历史名称汇总购买件数，未扣除退件。</p>
        <ul>
          <li v-for="product in data.common_products" :key="product.product_name">
            {{ product.product_name }} <strong>{{ product.quantity }} 件</strong>
          </li>
        </ul>
      </div>
      <div class="address-list-heading">
        <h3>历史订单</h3>
        <button class="text-action" :disabled="loading" @click="load">刷新订单</button>
      </div>
      <div class="customer-orders-table">
        <table>
          <thead>
            <tr>
              <th>订单号</th>
              <th>履约状态</th>
              <th>应收金额</th>
              <th>已核实创建时间</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="order in data.orders.items" :key="order.id">
              <td>
                <a :href="`/orders?order_id=${order.id}`">{{ order.order_number }}</a>
              </td>
              <td>{{ statuses[order.status] ?? order.status }}</td>
              <td>{{ money(order.total) }}</td>
              <td>{{ order.created_at ? dateTime(order.created_at) : '历史时间待核实' }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-if="!data.orders.total" class="module-empty">该客户还没有订单。</p>
      <AppPagination
        :page="page"
        :page-size="10"
        :page-count="pageCount"
        :total="data.orders.total"
        :loading="loading"
        @change="changePage"
      />
    </template>
  </section>
</template>
