import { request } from '../../../shared/api/httpClient'

export const fetchPrintDocument = (jobId: number, layout: 'orders' | 'summary') =>
  request<string>(`/order-operations/print-jobs/${jobId}/html?layout=${layout}`, 'GET', undefined, {
    responseType: 'text',
    fallback: '打印请求已登记，文档加载失败。可从订单详情查看历史文档。',
  })
