import { request } from '../../../shared/api/httpClient'
import type { CustomerDetail, CustomerProfile } from '../model/details'
export const fetchCustomerDetail = (id: number, page = 1) =>
  request<CustomerDetail>(`/customers/${id}?page=${page}&page_size=10`)
export const saveCustomerProfile = (id: number, tags: string[], note: string, revision: number) =>
  request<CustomerProfile>(`/customers/${id}/profile`, 'PUT', {
    tags,
    note,
    expected_revision: revision,
  })
