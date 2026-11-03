// Web Audio 负责PCM采集和排队播放，用户取消、离开页面时释放音频设备。
export class PcmRecorder {
  private generation = 0
  private stream: MediaStream | null = null
  private context: AudioContext | null = null
  private node: AudioWorkletNode | null = null
  private source: MediaStreamAudioSourceNode | null = null
  private sink: GainNode | null = null
  private flushed: (() => void) | null = null
  private stopping: Promise<void> | null = null

  async start(onAudio: (buffer: ArrayBuffer, level: number) => void) {
    const generation = ++this.generation
    if (this.stopping) await this.stopping
    if (generation !== this.generation) return false
    if (!navigator.mediaDevices?.getUserMedia || !window.AudioContext) {
      throw new Error('当前浏览器无法录音，请使用 localhost 或 HTTPS 打开，或改用文字输入。')
    }
    let stream: MediaStream | null = null
    let context: AudioContext | null = null
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      })
      if (generation !== this.generation) {
        stream.getTracks().forEach((track) => track.stop())
        return false
      }
      context = new AudioContext({ sampleRate: 16000 })
      await context.audioWorklet.addModule(`${import.meta.env.BASE_URL}agent-pcm-worklet.js`)
      await context.resume()
      if (generation !== this.generation) {
        stream.getTracks().forEach((track) => track.stop())
        await context.close()
        return false
      }
      const node = new AudioWorkletNode(context, 'agent-pcm-capture')
      node.port.onmessage = ({ data }) => {
        if (data.type === 'audio') onAudio(data.buffer, data.level)
        if (data.type === 'flushed') this.flushed?.()
      }
      const source = context.createMediaStreamSource(stream)
      const sink = context.createGain()
      sink.gain.value = 0
      source.connect(node).connect(sink).connect(context.destination)
      this.stream = stream
      this.context = context
      this.node = node
      this.source = source
      this.sink = sink
      return true
    } catch (reason) {
      stream?.getTracks().forEach((track) => track.stop())
      if (context && context.state !== 'closed') await context.close().catch(() => {})
      if (generation !== this.generation) return false
      if (reason instanceof DOMException && reason.name === 'NotAllowedError')
        throw new Error('麦克风权限未开启，请允许访问后再次按住说话。')
      throw reason instanceof Error ? reason : new Error('麦克风启动失败，请使用文字输入。')
    }
  }

  stop(discard = false) {
    ++this.generation
    if (this.stopping) return this.stopping
    this.stopping = this.cleanup(discard).finally(() => {
      this.stopping = null
    })
    return this.stopping
  }

  private async cleanup(discard: boolean) {
    const node = this.node
    if (node) {
      await new Promise<void>((resolve) => {
        const timer = setTimeout(resolve, 600)
        this.flushed = () => {
          clearTimeout(timer)
          resolve()
        }
        node.port.postMessage({ type: 'stop', discard })
      })
      node.port.onmessage = null
      node.disconnect()
    }
    this.source?.disconnect()
    this.sink?.disconnect()
    this.stream?.getTracks().forEach((track) => track.stop())
    const context = this.context
    this.node = this.source = this.sink = this.stream = this.context = this.flushed = null
    if (context && context.state !== 'closed') await context.close().catch(() => {})
  }
}

export class PcmPlayer {
  private context: AudioContext | null = null
  private nodes = new Set<AudioBufferSourceNode>()
  private nextTime = 0
  private remainder: number | null = null
  private sampleRate = 24000
  private onPlaying: (playing: boolean) => void

  constructor(onPlaying: (playing: boolean) => void) {
    this.onPlaying = onPlaying
  }

  async unlock() {
    this.context ??= new AudioContext()
    if (this.context.state === 'suspended') await this.context.resume()
  }

  start(sampleRate = 24000) {
    this.stop()
    this.sampleRate = sampleRate
  }

  push(buffer: ArrayBuffer) {
    const context = this.context
    if (!context || context.state !== 'running') return false
    const bytes = new Uint8Array(buffer)
    const data = new Uint8Array(bytes.length + (this.remainder === null ? 0 : 1))
    let offset = 0
    if (this.remainder !== null) data[offset++] = this.remainder
    data.set(bytes, offset)
    const length = Math.floor(data.length / 2)
    this.remainder = data.length % 2 ? (data[data.length - 1] ?? null) : null
    if (!length) return true
    const audio = context.createBuffer(1, length, this.sampleRate)
    const samples = audio.getChannelData(0)
    const view = new DataView(data.buffer)
    for (let i = 0; i < length; i++) samples[i] = view.getInt16(i * 2, true) / 32768
    const node = context.createBufferSource()
    node.buffer = audio
    node.connect(context.destination)
    node.onended = () => {
      this.nodes.delete(node)
      node.disconnect()
      this.onPlaying(this.nodes.size > 0)
    }
    this.nodes.add(node)
    node.start(Math.max(context.currentTime + 0.02, this.nextTime))
    this.nextTime = Math.max(context.currentTime + 0.02, this.nextTime) + audio.duration
    this.onPlaying(true)
    return true
  }

  stop() {
    for (const node of this.nodes) {
      node.onended = null
      node.stop()
      node.disconnect()
    }
    this.nodes.clear()
    this.nextTime = 0
    this.remainder = null
    this.onPlaying(false)
  }

  dispose() {
    this.stop()
    void this.context?.close().catch(() => {})
    this.context = null
  }
}
