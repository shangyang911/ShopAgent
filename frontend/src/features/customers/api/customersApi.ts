import type { CustomerRecord, CustomerWrite, AddressRecord, AddressWrite } from '../model/types'

import { request } from '../../../shared/api/httpClient'

export const fetchCustomers = () => request<CustomerRecord[]>('/customers')
export const fetchCustomerAddresses = (customerId: number) =>
  request<AddressRecord[]>(`/customers/${customerId}/addresses`)
export const createCustomer = (values: CustomerWrite) =>
  request<CustomerRecord>('/customers', 'POST', values)
export const updateCustomer = (id: number, values: CustomerWrite) =>
  request<CustomerRecord>(`/customers/${id}`, 'PUT', values)
export const createCustomerAddress = (values: AddressWrite & { customer_id: number }) =>
  request<AddressRecord>('/addresses', 'POST', values)
export const updateCustomerAddress = (id: number, values: AddressWrite) =>
  request<AddressRecord>(`/addresses/${id}`, 'PUT', values)
export const deleteCustomerAddress = (id: number) => request<void>(`/addresses/${id}`, 'DELETE')
