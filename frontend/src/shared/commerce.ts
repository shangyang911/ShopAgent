// 金额先转为整数“分”再计算，避免 0.1 + 0.2 的浮点误差；显示日期统一用上海时区。
export function cents(value: string | number): bigint {
  const text = String(value || 0)
  if (!/^-?\d+(\.\d{0,2})?$/.test(text)) return 0n
  const negative = text.startsWith('-')
  const [whole = '0', fraction = ''] = text.replace(/^-/, '').split('.')
  const amount = BigInt(whole || '0') * 100n + BigInt((fraction + '00').slice(0, 2))
  return negative ? -amount : amount
}

export function decimal(value: bigint): string {
  const sign = value < 0n ? '-' : ''
  const amount = value < 0n ? -value : value
  return `${sign}${amount / 100n}.${String(amount % 100n).padStart(2, '0')}`
}

export function money(value: string | number | null | undefined): string {
  if (value == null) return '—'
  const [whole, fraction] = decimal(cents(value)).split('.')
  return `¥${whole!.replace(/\B(?=(\d{3})+(?!\d))/g, ',')}.${fraction}`
}

export function dateTime(value?: string | null): string {
  // 兼容后端无时区的 UTC 时间字符串：补 Z 后再转为上海时间，避免被当成本机时间。
  if (!value) return '时间待核实'
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(new Date(value.endsWith('Z') || /[+-]\d\d:\d\d$/.test(value) ? value : `${value}Z`))
}

export function shanghaiToday(): string {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(new Date())
  const get = (type: string) => parts.find((p) => p.type === type)!.value
  return `${get('year')}-${get('month')}-${get('day')}`
}

export function addDays(day: string, count: number): string {
  const date = new Date(`${day}T00:00:00Z`)
  date.setUTCDate(date.getUTCDate() + count)
  return date.toISOString().slice(0, 10)
}

export function dayBoundary(day: string): string {
  return `${day}T00:00:00+08:00`
}
