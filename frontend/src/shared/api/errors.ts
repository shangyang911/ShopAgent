// FastAPI 的 detail 可能是业务错误字符串，也可能是 422 参数校验错误数组。
// 共享请求层解析错误，避免各功能漏掉字段名或把对象直接显示成字符串。
export function apiErrorDetail(detail: unknown): string {
  if (typeof detail === 'string') return detail
  if (!Array.isArray(detail)) return ''
  return detail
    .flatMap((item: unknown) => {
      if (!item || typeof item !== 'object' || !('msg' in item) || typeof item.msg !== 'string')
        return []
      const location =
        'loc' in item && Array.isArray(item.loc)
          ? item.loc
              .slice(1)
              .filter((part) => typeof part === 'string' || typeof part === 'number')
              .join('.')
          : ''
      return [location ? `${location}: ${item.msg}` : item.msg]
    })
    .join('；')
}
