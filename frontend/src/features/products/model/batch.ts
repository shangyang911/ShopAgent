// 批量操作不接收任意字段，防止绕过库存和多规格价格的专用业务流程。
export type ProductBatchAction = 'activate' | 'deactivate' | 'archive' | 'favorite' | 'unfavorite'
export interface ProductBatchPreview {
  snapshot: string
  items: { id: number; name: string; before: string; after: string }[]
}
