import type { AuthUser, AuthStatus } from '../model/types'

import { ApiError, request } from '../../../shared/api/httpClient'

export const fetchAuthStatus = () => request<AuthStatus>('/auth/status')

export async function fetchCurrentUser(): Promise<AuthUser | null> {
  try {
    return await request<AuthUser>('/auth/me')
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) return null
    throw error
  }
}

export const login = (username: string, password: string) =>
  request<AuthUser>('/auth/login', 'POST', { username, password })
export const registerFirstAdmin = (username: string, password: string) =>
  request<AuthUser>('/auth/register', 'POST', { username, password })
export const logout = () => request<void>('/auth/logout', 'POST')
