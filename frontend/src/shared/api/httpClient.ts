// [共享网络层] 统一 API Base URL、Cookie 会话和 Axios 错误提示。
import axios, { type AxiosRequestConfig } from 'axios'
import { API_BASE } from './config'
import { apiErrorDetail } from './errors'

export const httpClient = axios.create({
  baseURL: API_BASE,
  withCredentials: true,
  headers: { Accept: 'application/json' },
})

export function apiErrorMessage(error: unknown, fallback: string): string {
  if (axios.isAxiosError<{ detail?: unknown }>(error)) {
    const detail = apiErrorDetail(error.response?.data?.detail)
    if (detail) return detail
    return `${fallback} (${error.response?.status ?? 'network error'})`
  }
  return error instanceof Error ? error.message : fallback
}

export class ApiError extends Error {
  // 保留 HTTP 状态供业务区分登录失效、版本冲突等情况，同时统一可读错误文案。
  readonly status: number | undefined

  constructor(error: unknown, fallback: string) {
    super(apiErrorMessage(error, fallback))
    this.name = 'ApiError'
    this.status = axios.isAxiosError(error) ? error.response?.status : undefined
  }
}

type RequestOptions = AxiosRequestConfig & { fallback?: string }

// 返回响应数据；业务模块负责路径、参数和类型，共享层负责 Cookie 与错误转换。
export async function request<T>(
  path: string,
  method = 'GET',
  body?: unknown,
  options: RequestOptions = {},
): Promise<T> {
  const { fallback = '请求失败', ...config } = options
  try {
    return (await httpClient.request<T>({ ...config, url: path, method, data: body })).data
  } catch (error) {
    if (axios.isAxiosError(error) && error.response?.data instanceof Blob) {
      // 下载接口预期返回文件，但出错时可能返回 JSON；先解析它，才能展示后端的业务原因。
      try {
        error.response.data = JSON.parse(await error.response.data.text())
      } catch {
        // 文件下载失败时，网关仍可能返回 HTML 或空响应。
      }
    }
    throw new ApiError(error, fallback)
  }
}
