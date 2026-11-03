import { request } from './httpClient'

export async function downloadCsv(path: string, filename: string): Promise<void> {
  const blob = await request<Blob>(path, 'GET', undefined, {
    responseType: 'blob',
    fallback: '导出失败',
  })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
