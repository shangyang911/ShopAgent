export interface AuthUser {
  id: number
  username: string
  created_at: string
}

export interface AuthStatus {
  setup_required: boolean
}
