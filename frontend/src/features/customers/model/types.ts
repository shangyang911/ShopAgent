export interface CustomerRecord {
  id: number
  name: string
  phone: string
  email: string | null
}

export interface CustomerWrite {
  name: string
  phone: string
  email: string | null
}

export interface AddressRecord {
  id: number
  customer_id: number
  recipient_name: string
  phone: string
  country: string
  province: string
  city: string
  district: string
  detail: string
  postal_code: string | null
  longitude: number | null
  latitude: number | null
  is_default: boolean
}

export interface AddressWrite {
  customer_id?: number
  recipient_name: string
  phone: string
  country: string
  province: string
  city: string
  district: string
  detail: string
  postal_code: string | null
  longitude: number | null
  latitude: number | null
  is_default: boolean
}
