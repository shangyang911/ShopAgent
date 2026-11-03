import assert from 'node:assert/strict'
import test from 'node:test'
import { loadApiModule } from './helpers/loadApiModule.mjs'

const { estimateDrivingTime, formatDrivingDuration } = loadApiModule(
  'features/shipping/model/drivingTime.ts',
)

test('route travel estimates add only intermediate 20-minute breaks every four driving hours', () => {
  for (const [seconds, rests] of [[0, 0], [1, 0], [14399, 0], [14400, 0], [14401, 1], [28800, 1], [28801, 2], [43200, 2], [43201, 3]]) {
    const result = estimateDrivingTime(seconds)
    assert.equal(result.restCount, rests)
    assert.equal(result.totalSeconds, seconds + rests * 20 * 60)
  }
})

test('the screenshot example includes three breaks and converts 758 minutes to 13 hours 38 minutes total', () => {
  const result = estimateDrivingTime(758 * 60)
  assert.equal(result.restCount, 3)
  assert.equal(formatDrivingDuration(result.drivingSeconds), '12 小时 38 分钟')
  assert.equal(formatDrivingDuration(result.restSeconds), '1 小时')
  assert.equal(formatDrivingDuration(result.totalSeconds), '13 小时 38 分钟')
})

test('durations use 24-hour days and round display minutes up without rounding the break threshold', () => {
  assert.equal(formatDrivingDuration(24 * 3600), '1 天')
  const result = estimateDrivingTime(24 * 3600)
  assert.equal(result.restCount, 5)
  assert.equal(formatDrivingDuration(result.totalSeconds), '1 天 1 小时 40 分钟')
  assert.equal(formatDrivingDuration(49 * 3600 + 61), '2 天 1 小时 2 分钟')
  assert.equal(formatDrivingDuration(0), '0 分钟')
  assert.equal(formatDrivingDuration(1), '1 分钟')
  assert.equal(estimateDrivingTime(14400.1).restCount, 1)
})

test('missing or invalid route times do not appear as zero-duration journeys', () => {
  for (const seconds of [null, undefined, NaN, Infinity, -1, '758']) {
    assert.equal(estimateDrivingTime(seconds), null)
    assert.equal(formatDrivingDuration(seconds), '时间待核实')
  }
})
