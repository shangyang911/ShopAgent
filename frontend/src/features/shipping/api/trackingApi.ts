// 物流接口层：只描述路径、参数和返回类型；Cookie 与错误转换由 shared/api/httpClient 统一负责。
import type { TrackingSnapshot } from '../model/tracking'

import { request } from '../../../shared/api/httpClient'

export const fetchTracking = (id: number, packageNo = 1) =>
  // GET 只读本地记录；页面刷新和客服查询均不会因此额外访问物流平台。
  request<TrackingSnapshot>(
    packageNo === 1
      ? `/shipping/orders/${id}/tracking`
      : `/shipping/orders/${id}/packages/${packageNo}/tracking`,
  )
export const syncTracking = (id: number, packageNo = 1) =>
  // POST 表达明确的同步动作，后端仍可能因限频返回 cached，而不是再次查询平台。
  request<TrackingSnapshot>(
    packageNo === 1
      ? `/shipping/orders/${id}/tracking/sync`
      : `/shipping/orders/${id}/packages/${packageNo}/tracking/sync`,
    'POST',
  )

// 整单汇总用于选择包裹，原单包裹接口继续兼容历史页面和测试。
export const fetchPackageTracking = (id: number) =>
  request<{
    items: (TrackingSnapshot & { package_no: number })[]
    package_count: number
    signed_count: number
    summary: string
  }>(`/shipping/orders/${id}/tracking-packages`)

export const trackingStatusNames: Record<string, string> = {
  unknown: '暂无可确认轨迹',
  collected: '已揽收',
  in_transit: '运输中',
  out_for_delivery: '派件中',
  signed: '平台显示已签收',
  exception: '物流异常',
  returning: '退回 / 退签中',
  customs: '清关中',
}
