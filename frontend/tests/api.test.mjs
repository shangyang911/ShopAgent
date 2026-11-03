import assert from 'node:assert/strict'
import test from 'node:test'
import { loadApiModule } from './helpers/loadApiModule.mjs'

const i18n = { useI18n: () => ({ t: (message) => message }) }
const imports = { '../../../shared/i18n': i18n }
const validation = { detail: [{ loc: ['body', 'items', 0, 'quantity'], msg: '必须为正整数' }] }

for (const [path, call] of [
  ['features/orders/api/ordersApi.ts', (api) => api.createOrder({})],
  ['features/customers/api/customersApi.ts', (api) => api.createCustomer({})],
  ['features/auth/api/authApi.ts', (api) => api.login('admin', 'password')],
  ['shared/api/httpClient.ts', (api) => api.request('/inventory/inbounds', 'POST', {})],
  ['features/products/api/productsApi.ts', (api) => api.updateProduct(1, {})],
]) {
  test(`${path} displays validation fields and keeps cookie credentials and configured API prefix`, async () => {
    const requests = []
    const api = loadApiModule(path, {
      apiBase: 'https://api.test/store/api',
      imports,
      fetch: async (url, options) => {
        requests.push({ url, options })
        return { ok: false, status: 422, json: async () => validation }
      },
    })
    await assert.rejects(call(api), /items\.0\.quantity: 必须为正整数/)
    assert.equal(requests.length, 1)
    assert.ok(requests[0].url.startsWith('https://api.test/store/api/'))
    assert.equal(requests[0].options.credentials, 'include')
    if (path.includes('products')) {
      assert.ok(requests[0].options.body instanceof FormData)
      // multipart boundary 必须由浏览器设置，不能被通用 JSON 请求头覆盖。
      assert.equal(requests[0].options.headers?.['Content-Type'], undefined)
    }
  })
}

test('empty or non-JSON errors retain the operation fallback and HTTP status', async () => {
  for (const json of [
    async () => {
      throw new SyntaxError('HTML response')
    },
    async () => null,
    async () => ({ detail: {} }),
  ]) {
    const api = loadApiModule('features/orders/api/ordersApi.ts', {
      fetch: async () => ({ ok: false, status: 502, json }),
    })
    await assert.rejects(api.fetchOrders(), /订单列表加载失败 \(502\)/)
  }
})

test('Axios uses the same validation format and safely falls back for unknown details', () => {
  const api = loadApiModule('shared/api/httpClient.ts', {
    imports: { axios: { create: (options) => ({ defaults: options }), isAxiosError: () => true } },
  })
  assert.equal(
    api.apiErrorMessage({ response: { data: validation } }, '请求失败'),
    'items.0.quantity: 必须为正整数',
  )
  assert.equal(
    api.apiErrorMessage({ response: { status: 502, data: { detail: {} } } }, '请求失败'),
    '请求失败 (502)',
  )
})

test('expired login still returns null instead of surfacing a startup error', async () => {
  const api = loadApiModule('features/auth/api/authApi.ts', {
    fetch: async () => ({ ok: false, status: 401 }),
  })
  assert.equal(await api.fetchCurrentUser(), null)
})

test('order retries retain idempotency headers and repeated status filters', async () => {
  const requests = []
  const api = loadApiModule('features/orders/api/ordersApi.ts', {
    fetch: async (url, options) => {
      requests.push({ url, options })
      return { ok: true, json: async () => ({}) }
    },
  })
  await api.createOrder({ items: [] }, 'retry-key-123')
  assert.equal(requests[0].options.headers['Idempotency-Key'], 'retry-key-123')
  await api.fetchOrders(undefined, 2, 20, { statuses: ['cancelled', 'refunded'] })
  assert.deepEqual(new URL(requests[1].url).searchParams.getAll('statuses'), [
    'cancelled',
    'refunded',
  ])
})

test('orderable products reuse catalogue pagination and preserve sellability filtering', async () => {
  const pages = []
  const api = loadApiModule('features/products/api/productsApi.ts', {
    imports,
    fetch: async (url) => {
      const params = new URL(url).searchParams
      const page = Number(params.get('page'))
      pages.push(page)
      assert.equal(params.get('page_size'), '48')
      assert.equal(params.get('sort'), 'featured')
      const items =
        page === 1
          ? [
              { id: 1, status: 'Active', stock: 2 },
              { id: 2, status: 'Draft', stock: 2 },
            ]
          : [
              { id: 3, status: 'Low stock', stock: 1 },
              { id: 4, status: 'Out of stock', stock: 0 },
              { id: 5, status: 'Inactive', stock: 10 },
              { id: 6, status: 'Archived', stock: 10 },
            ]
      return { ok: true, json: async () => ({ total: 49, items }) }
    },
  })
  const products = await api.fetchOrderableProducts()
  assert.deepEqual(pages, [1, 2])
  assert.deepEqual(
    Array.from(products, (product) => product.id),
    [1, 3],
  )
})

test('shared Axios requests preserve text responses and empty DELETE responses', async () => {
  const requests = []
  const api = loadApiModule('features/orders/api/printingApi.ts', {
    fetch: async (url, options) => {
      requests.push({ url, options })
      return { ok: true, text: async () => '<html>配货单</html>' }
    },
  })
  assert.equal(await api.fetchPrintDocument(7, 'summary'), '<html>配货单</html>')
  assert.match(requests[0].url, /print-jobs\/7\/html\?layout=summary/)
  const customers = loadApiModule('features/customers/api/customersApi.ts', {
    fetch: async () => ({
      ok: true,
      status: 204,
      json: async () => {
        throw new SyntaxError('No content')
      },
    }),
  })
  await customers.deleteCustomerAddress(7)
})

test('CSV errors decode JSON blobs using the common validation format', async () => {
  const api = loadApiModule('shared/api/httpClient.ts', {
    fetch: async () => ({
      ok: false,
      status: 422,
      blob: async () => new Blob([JSON.stringify(validation)], { type: 'application/json' }),
    }),
  })
  await assert.rejects(
    api.request('/orders/export', 'GET', undefined, { responseType: 'blob' }),
    /items\.0\.quantity: 必须为正整数/,
  )
})
