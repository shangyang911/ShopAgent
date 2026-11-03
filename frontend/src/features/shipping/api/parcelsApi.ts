import type { PackageSnapshot } from '../model/parcels'
import { request } from '../../../shared/api/httpClient'

export const fetchPackages = (id: number) =>
  request<PackageSnapshot>(`/shipping/orders/${id}/packages`)
