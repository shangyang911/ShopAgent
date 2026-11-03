import { request } from '../../../shared/api/httpClient'
import type { ProductBatchAction, ProductBatchPreview } from '../model/batch'
export const previewProductBatch = (ids: number[], action: ProductBatchAction) =>
  request<ProductBatchPreview>('/products/batch/preview', 'POST', { ids, action })
export const applyProductBatch = (
  ids: number[],
  action: ProductBatchAction,
  snapshot: string,
  idempotencyKey: string,
) =>
  request<{ count: number }>('/products/batch/apply', 'POST', {
    ids,
    action,
    snapshot,
    idempotency_key: idempotencyKey,
  })
