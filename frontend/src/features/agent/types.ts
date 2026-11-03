export interface AgentCapabilities {
  chat: boolean
  voice_input: boolean
  voice_output: boolean
  knowledge_base: boolean
  handoff: string
  identity: string
  business_tools: string[]
}

export interface AgentStatus {
  capabilities: AgentCapabilities
  limits: { audio_seconds: number; text_characters: number; confirmation_seconds: number }
}

export interface AgentAction {
  id: string
  kind: string
  state: 'pending' | 'completed' | 'declined' | 'expired' | 'superseded' | 'cancelled' | 'stale'
  order_id: number
  order_number: string
  total: string
  reason: string
  effect: string
  title: string
  expires_at: string
  result?: AgentActionResult | null
}

export interface AgentActionResult {
  action_id: string
  order_id: number
  order_number: string
  status: string
  reason: string
}

export interface AgentTicket {
  id: string
  conversation_id: string
  summary: string
  state: 'pending' | 'in_progress' | 'resolved'
  created_at: string
  transcript?: AgentMessage[]
}

export interface KnowledgeSource {
  id: string
  title: string
  version?: string | null
  effective_from?: string | null
  effective_until?: string | null
  section?: string | null
  content_hash?: string | null
  excerpt?: string | null
  document_id?: string | null
}

export interface AgentMessage {
  trace_id?: string
  copilot?: { href: string; label: string }
  role: 'user' | 'assistant'
  text: string
  request_id: string
  created_at: string
  speech_text?: string
  sources?: KnowledgeSource[]
  action?: AgentAction
  action_result?: AgentActionResult
  ticket?: AgentTicket
  delivery?: 'sending' | 'sent' | 'interrupted' | 'failed'
  source?: 'text' | 'voice'
}

export interface AgentConversation {
  id: string
  messages: AgentMessage[]
  actions: AgentAction[]
  tickets: AgentTicket[]
  busy: boolean
}
