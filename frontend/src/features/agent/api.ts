import { apiErrorMessage, httpClient } from '../../shared/api/httpClient'
import type { AgentConversation, AgentStatus, AgentTicket } from './types'

export class AgentApiError extends Error {
  status: number | undefined
  constructor(message: string, status?: number) {
    super(message)
    this.status = status
  }
}

async function call<T>(path: string, method: 'GET' | 'POST' | 'DELETE' = 'GET'): Promise<T> {
  try {
    return (
      await httpClient.request<T>({
        url: `/agent${path}`,
        method,
        ...(method === 'DELETE' ? { timeout: 15000 } : {}),
      })
    ).data
  } catch (reason) {
    const status = (reason as { response?: { status?: number } })?.response?.status
    throw new AgentApiError(apiErrorMessage(reason, '客服服务连接失败'), status)
  }
}

export const fetchAgentStatus = () => call<AgentStatus>('/status')
export const createAgentConversation = () => call<{ id: string }>('/conversations', 'POST')
export const fetchAgentConversation = (id: string) =>
  call<AgentConversation>(`/conversations/${id}`)
export const fetchAgentTicket = (id: string) => call<AgentTicket>(`/tickets/${id}`)
export const archiveAgentConversation = (id: string) => call<void>(`/conversations/${id}`, 'DELETE')

export function agentSocketUrl(id: string) {
  const url = new URL(httpClient.defaults.baseURL ?? '/api', window.location.origin)
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:'
  url.pathname = `${url.pathname.replace(/\/$/, '')}/agent/ws`
  url.search = new URLSearchParams({ conversation_id: id }).toString()
  return url.toString()
}
