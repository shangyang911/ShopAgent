// 按《道路交通安全法实施条例》第六十二条（七）估算途中休息。
// 仅计算 4 小时 / 20 分钟这一项，不代表已校验全部运输或驾驶要求。
const DRIVING_BLOCK_SECONDS = 4 * 60 * 60
const REST_SECONDS = 20 * 60

export function estimateDrivingTime(seconds: number | null | undefined) {
  if (typeof seconds !== 'number' || !Number.isFinite(seconds) || seconds < 0) return null
  const drivingSeconds = Math.ceil(seconds)
  // 恰好在 4 小时边界到达终点时，不追加一次途中休息。
  const restCount = Math.max(0, Math.ceil(drivingSeconds / DRIVING_BLOCK_SECONDS) - 1)
  const restSeconds = restCount * REST_SECONDS
  return { drivingSeconds, restCount, restSeconds, totalSeconds: drivingSeconds + restSeconds }
}

export function formatDrivingDuration(seconds: number | null | undefined): string {
  if (typeof seconds !== 'number' || !Number.isFinite(seconds) || seconds < 0) return '时间待核实'
  const totalMinutes = Math.ceil(seconds / 60)
  const days = Math.floor(totalMinutes / (24 * 60))
  const hours = Math.floor((totalMinutes % (24 * 60)) / 60)
  const minutes = totalMinutes % 60
  return (
    [days ? `${days} 天` : '', hours ? `${hours} 小时` : '', minutes ? `${minutes} 分钟` : '']
      .filter(Boolean)
      .join(' ') || '0 分钟'
  )
}
