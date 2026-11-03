import { request } from '../../../shared/api/httpClient'
import type { Product } from '../model/types'

export const fetchFavorites = () => request<Product[]>('/products/favorites')
export const addFavorite = (id: number) => request<void>(`/products/favorites/${id}`, 'PUT')
export const removeFavorite = (id: number) => request<void>(`/products/favorites/${id}`, 'DELETE')
