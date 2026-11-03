import { request } from '../../../shared/api/httpClient'
import type { AfterSale, AfterSaleSnapshot } from '../model/types'

// 写动作统一走售后入口；组件不会通过更改订单状态冒充实际退款。
const path = (orderId: number) => `/orders/${orderId}/aftersales`
// 读取快照不触发资金或库存变化，允许订单详情反复刷新。
export const fetchAfterSales = (orderId: number) => request<AfterSaleSnapshot>(path(orderId))
export const createAfterSale = (orderId: number, payload: object) =>
  request<AfterSale>(path(orderId), 'POST', payload)
// 凭据登记与支付通道执行分开；这里只记录已经人工核实的退款。
export const writeRefund = (orderId: number, caseId: number, payload: object) =>
  request<AfterSale>(`${path(orderId)}/${caseId}/refunds`, 'POST', payload)
export const inspectReturn = (orderId: number, caseId: number, payload: object) =>
  request<AfterSale>(`${path(orderId)}/${caseId}/inspection`, 'POST', payload)
// 撤销带读取时的版本，防止覆盖其他窗口已经发生的退款或验收。
export const cancelAfterSale = (orderId: number, caseId: number, revision: number) =>
  request<AfterSale>(`${path(orderId)}/${caseId}/cancel`, 'POST', { expected_revision: revision })
