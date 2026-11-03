// [商品列表页 / API 数据契约] 与后端 schemas/product.py 的商品、分页响应和筛选选项结构对应。
export interface Product {
  id: number
  name: string
  category: string
  price: number
  stock: number
  status: string
  inventory_status?: 'normal' | 'low_stock' | 'out_of_stock' | 'unmonitored'
  image_url: string
  color: string
  rating: number
  reviews: number
  shipping: string
}

export type ProductWrite = Omit<Product, 'id' | 'image_url' | 'inventory_status'>

export interface ProductListResponse {
  items: Product[]
  total: number
  page: number
  page_size: number
}

export interface ProductOptions {
  categories: string[]
  colors: string[]
  shipping_options: string[]
}

export interface ProductFilters {
  q: string
  category: string[]
  color: string[]
  shipping: string[]
  min_price?: number
  max_price?: number
  status?: string
  sort: string
  page: number
  page_size: number
}

export interface ProductSKU {
  id: number
  product_id: number
  code: string
  color: string
  size: string
  price: string
  stock: number
  reserved: number
  active: boolean
  is_default: boolean
  revision: number
  label: string
  available_quantity: number
  reserved_quantity: number
  on_hand_quantity: number
}

export interface ProductImage {
  id: number | null
  url: string
  alt: string
  color: string
  position: number
  role: string
}

export interface ProductDetail extends Product {
  revision: number
  images: ProductImage[]
  media: ProductImage[]
  skus: ProductSKU[]
  description: {
    type: 'text' | 'image'
    text: string
    image_id: number | null
    url?: string | null
  }[]
  parameters: { name: string; value: string }[]
  options: { colors: string[]; sizes: string[] }
}
