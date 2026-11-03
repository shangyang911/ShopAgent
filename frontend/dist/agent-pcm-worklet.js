// 麦克风音频按100ms分块，重采样为16kHz PCM；原始音频不会写入浏览器存储。
class AgentPcmCapture extends AudioWorkletProcessor {
  constructor() {
    super()
    this.active = true
    this.samples = new Int16Array(1600)
    this.count = 0
    this.phase = 0
    this.sum = 0
    this.sumCount = 0
    this.power = 0
    this.port.onmessage = ({ data }) => {
      if (data.type !== 'stop') return
      this.active = false
      if (!data.discard) this.flush()
      this.port.postMessage({ type: 'flushed' })
    }
  }
  flush() {
    if (!this.count) return
    const bytes = new ArrayBuffer(this.count * 2)
    const view = new DataView(bytes)
    for (let i = 0; i < this.count; i++) view.setInt16(i * 2, this.samples[i], true)
    this.port.postMessage(
      { type: 'audio', buffer: bytes, level: Math.sqrt(this.power / this.count) },
      [bytes],
    )
    this.count = 0
    this.power = 0
  }
  process(inputs) {
    if (!this.active) return false
    const input = inputs[0]?.[0]
    if (!input) return true
    for (const value of input) {
      this.sum += value
      this.sumCount++
      this.phase += 16000
      if (this.phase >= sampleRate) {
        const sample = Math.max(-1, Math.min(1, this.sum / this.sumCount))
        this.samples[this.count++] = Math.round(sample * (sample < 0 ? 32768 : 32767))
        this.power += sample * sample
        this.phase -= sampleRate
        this.sum = 0
        this.sumCount = 0
        if (this.count === this.samples.length) this.flush()
      }
    }
    return true
  }
}
registerProcessor('agent-pcm-capture', AgentPcmCapture)
