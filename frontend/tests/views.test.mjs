import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { setImmediate } from 'node:timers/promises'
import { runInNewContext } from 'node:vm'
import test from 'node:test'
import ts from 'typescript'
import * as Vue from 'vue'
import { compileScript, parse } from 'vue/compiler-sfc'
import { loadApiModule } from './helpers/loadApiModule.mjs'

function inventoryServices(overrides = {}) {
  return {
    request: async (path) => {
      if (path === '/inventory/settings') return { default_threshold: 5 }
      if (path === '/products/options') return { categories: [] }
      return { items: [], total: 0, page: 1, page_size: 20 }
    },
    ...overrides,
  }
}

test('inventory replenishment requires a user-entered positive integer quantity', async (t) => {
  const state = await mountView(t, 'InventoryView', inventoryServices())
  await state.openInbound(7)
  assert.equal(state.lines[0].product_id, 7)
  assert.equal(state.lines[0].quantity, '')
  for (const quantity of ['', 0, -1, 1.5, 1000001]) {
    state.lines[0].quantity = quantity
    await state.saveInbound()
    assert.match(state.inboundError, /正整数/)
    assert.equal(state.receipt, null)
  }
})

test('confirmed inventory receipt displays the original name without fetching current names', async (t) => {
  const paths = []
  const state = await mountView(
    t,
    'InventoryView',
    inventoryServices({
      request: async (path) => {
        paths.push(path)
        if (path === '/inventory/inbounds/9')
          return {
            id: 9,
            receipt_number: 'IN-9',
            status: 'confirmed',
            remark: '验收',
            items: [{ product_id: 7, product_name_snapshot: '发生时的商品名', quantity: 2 }],
          }
        return { items: [], total: 0 }
      },
    }),
  )
  paths.length = 0
  await state.openInbound(undefined, 9)
  assert.equal(state.editable, false)
  assert.equal(state.productChoices[0].name, '发生时的商品名')
  assert.deepEqual(paths, ['/inventory/inbounds/9'])
  await state.saveInbound()
  await state.confirmInbound()
  assert.equal(paths.length, 1)
})

test('failed inventory receipt fetch cannot become a new editable receipt', async (t) => {
  let writes = 0
  const state = await mountView(
    t,
    'InventoryView',
    inventoryServices({
      request: async (path, method = 'GET') => {
        if (method !== 'GET') writes++
        if (path === '/inventory/inbounds/9') throw new Error('单据读取失败')
        return { items: [], total: 0 }
      },
    }),
  )
  await state.openInbound(undefined, 9)
  assert.equal(state.inboundReady, false)
  assert.equal(state.editable, false)
  assert.equal(state.inboundError, '单据读取失败')
  await state.saveInbound()
  assert.equal(writes, 0)
})

test('inventory draft save blocks overlapping requests and adopts merged server lines', async (t) => {
  const pending = deferred()
  let writes = 0
  const state = await mountView(
    t,
    'InventoryView',
    inventoryServices({
      request: async (path, method = 'GET') => {
        if (method === 'POST') {
          writes++
          return pending.promise
        }
        return { items: [], total: 0 }
      },
    }),
  )
  await state.openInbound(7)
  state.lines = [
    { product_id: 7, quantity: 2 },
    { product_id: 7, quantity: 3 },
  ]
  const first = state.saveInbound()
  await state.saveInbound()
  assert.equal(writes, 1)
  pending.resolve({ id: 9, status: 'draft', items: [{ product_id: 7, quantity: 5 }] })
  await first
  assert.equal(state.lines.length, 1)
  assert.equal(state.totalQuantity, 5)
  assert.equal(state.savedSignature, state.signature)
})

test('inventory CSV exports use all current movement filters without page limits', async (t) => {
  const downloads = []
  const state = await mountView(
    t,
    'InventoryView',
    inventoryServices({
      downloadCsv: async (...args) => downloads.push(args),
    }),
  )
  state.tab = 'movements'
  state.productId = '7'
  state.movementType = 'inbound'
  state.referenceNumber = 'IN-9'
  state.actor = 'admin'
  state.startDate = '2026-10-01'
  state.endDate = '2026-10-02'
  state.page = 3
  await state.exportCsv()
  const params = new URLSearchParams(downloads[0][0].split('?')[1])
  assert.equal(params.get('product_id'), '7')
  assert.equal(params.get('movement_type'), 'inbound')
  assert.equal(params.get('reference_number'), 'IN-9')
  assert.equal(params.get('actor'), 'admin')
  assert.equal(params.get('start_at'), '2026-10-01T00:00:00+08:00')
  assert.equal(params.get('end_at'), '2026-10-03T00:00:00+08:00')
  assert.equal(params.has('page'), false)
  state.tab = 'inbounds'
  await state.exportCsv()
  assert.equal(downloads[1][0], '/inventory/inbounds/export')
})

test('inventory export failure is visible and allows another attempt', async (t) => {
  const state = await mountView(
    t,
    'InventoryView',
    inventoryServices({
      downloadCsv: async () => {
        throw new Error('导出暂时不可用')
      },
    }),
  )
  state.tab = 'movements'
  await state.exportCsv()
  assert.equal(state.exporting, false)
  assert.equal(state.error, '导出暂时不可用')
})

// Mount the real setup scripts with Vue's renderer, without a browser or network.
const renderer = Vue.createRenderer({
  createElement: () => ({}),
  createText: () => ({}),
  createComment: () => ({}),
  insert() { },
  remove() { },
  setText() { },
  setElementText() { },
  patchProp() { },
  parentNode: () => null,
  nextSibling: () => null,
})

function deferred() {
  let resolve, reject
  const promise = new Promise((yes, no) => {
    resolve = yes
    reject = no
  })
  return { promise, resolve, reject }
}

async function settle() {
  await Vue.nextTick()
  await setImmediate()
}

function compileComponent(path, services) {
  const source = readFileSync(new URL(path, import.meta.url), 'utf8')
  const { descriptor } = parse(source, { filename: path })
  const script = compileScript(descriptor, { id: path })
  const code = ts.transpileModule(script.content, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  }).outputText
  const module = { exports: {} }
  runInNewContext(code, {
    module,
    exports: module.exports,
    require: (id) => (id === 'vue' ? Vue : { loadAmapApi: async () => ({}), ...services }),
    URLSearchParams,
    Error,
    document: { hidden: false, addEventListener() { }, removeEventListener() { } },
    setInterval: () => 1,
    clearInterval() { },
    crypto: services.__crypto ?? { randomUUID: () => 'test-request-unique-key' },
    window: {
      confirm: () => true,
      location: { search: '' },
      history: { pushState() { }, replaceState() { } },
      addEventListener() { },
      removeEventListener() { },
      ...services.__window,
    },
    console,
  })
  return module.exports.default
}

async function mountView(t, name, overrides = {}) {
  const component = compileComponent(`../src/views/${name}.vue`, {
    ...commerceModule(),
    query: (values) =>
      new URLSearchParams(
        Object.entries(values).filter(([, value]) => value !== undefined && value !== ''),
      ).toString(),
    useI18n: () => ({ t: (key) => key }),
    fetchCustomers: async () => [],
    fetchCustomerAddresses: async () => [],
    fetchOrders: async () => ({ items: [], total: 0, page: 1, page_size: 20 }),
    fetchShippingWorkbench: async () => ({ items: [], total: 0, counts: {} }),
    ...overrides,
  })
  component.render = () => null
  const app = renderer.createApp(component, { username: 'test' })
  app.mount({})
  t.after(() => app.unmount())
  await settle()
  return app._instance.setupState
}

function commerceModule() {
  const source = readFileSync(new URL('../src/shared/commerce.ts', import.meta.url), 'utf8')
  const code = ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  }).outputText
  const module = { exports: {} }
  runInNewContext(code, { module, exports: module.exports })
  return module.exports
}

test('money arithmetic accumulates integer cents and handles incomplete form input', () => {
  const { cents, decimal, money, dateTime } = commerceModule()
  assert.equal(decimal(cents('0.10') + cents('0.20')), '0.30')
  assert.equal(money('9999999999.99'), '¥9,999,999,999.99')
  assert.equal(money(null), '—')
  assert.equal(cents('1e'), 0n)
  assert.match(dateTime('2026-10-26T16:00:00Z'), /2026\/10\/27 00:00:00/)
})

test('dashboard refresh failure preserves the last successful snapshot', async (t) => {
  const snapshot = { as_of: '2026-10-27T08:00:00Z', metrics: {}, trend: [] }
  let fail = false
  const state = await mountView(t, 'DashboardView', {
    request: async () => {
      if (fail) throw new Error('temporary failure')
      return snapshot
    },
  })
  assert.equal(state.data.as_of, snapshot.as_of)
  fail = true
  await state.load()
  assert.equal(state.data.as_of, snapshot.as_of)
  assert.equal(state.error, 'temporary failure')
  assert.equal(state.loading, false)
})

test('dashboard ignores a stale response after a newer date selection', async (t) => {
  const requests = []
  let delayed = false
  const state = await mountView(t, 'DashboardView', {
    request: async () => {
      if (!delayed) return { as_of: 'initial', metrics: {}, trend: [] }
      const item = deferred()
      requests.push(item)
      return item.promise
    },
  })
  delayed = true
  const first = state.load()
  const second = state.load()
  requests[1].resolve({ as_of: 'latest', metrics: {}, trend: [] })
  await second
  requests[0].resolve({ as_of: 'obsolete', metrics: {}, trend: [] })
  await first
  assert.equal(state.data.as_of, 'latest')
})

test('customer selection ignores a late address response for the previous customer', async (t) => {
  const requests = []
  const state = await mountView(t, 'CustomersView', {
    fetchCustomerAddresses: () => {
      const request = deferred()
      requests.push(request)
      return request.promise
    },
  })
  state.selectedId = 1
  await Vue.nextTick()
  state.selectedId = 2
  await Vue.nextTick()
  requests[1].resolve([{ id: 22, customer_id: 2 }])
  await settle()
  requests[0].resolve([{ id: 11, customer_id: 1 }])
  await settle()
  assert.equal(state.selectedId, 2)
  assert.equal(state.addresses[0].customer_id, 2)
})

test('an obsolete address failure does not hide the current customer', async (t) => {
  const requests = []
  const state = await mountView(t, 'CustomersView', {
    fetchCustomerAddresses: () => {
      const request = deferred()
      requests.push(request)
      return request.promise
    },
  })
  state.selectedId = 1
  await Vue.nextTick()
  state.selectedId = 2
  await Vue.nextTick()
  requests[1].resolve([{ id: 22, customer_id: 2 }])
  requests[0].reject(new Error('obsolete failure'))
  await settle()
  assert.equal(state.error, '')
  assert.equal(state.addresses[0].customer_id, 2)
})

test('refreshing the same customer also discards older address results', async (t) => {
  const requests = []
  const state = await mountView(t, 'CustomersView', {
    fetchCustomerAddresses: () => {
      const request = deferred()
      requests.push(request)
      return request.promise
    },
  })
  state.selectedId = 1
  await Vue.nextTick()
  const refresh = state.loadAddresses(1)
  requests[1].resolve([{ id: 12, customer_id: 1 }])
  await refresh
  requests[0].resolve([{ id: 11, customer_id: 1 }])
  await settle()
  assert.equal(state.addresses[0].id, 12)
})

for (const sameOrder of [false, true]) {
  test(`route requests keep the latest result (${sameOrder ? 'same' : 'different'} order)`, async (t) => {
    const requests = []
    const state = await mountView(t, 'ShippingView', {
      fetchDeliveryRoute: () => {
        const request = deferred()
        requests.push(request)
        return request.promise
      },
    })
    const latestId = sameOrder ? 1 : 2
    const first = state.planRoute({ id: 1 })
    const second = state.planRoute({ id: latestId })
    requests[1].resolve({ order_id: latestId, duration_seconds: 20 })
    await second
    requests[0].resolve({ order_id: 1, duration_seconds: 10 })
    await first
    assert.equal(state.routeOrderId, latestId)
    assert.equal(state.selectedRoute.order_id, latestId)
    assert.equal(state.selectedRoute.duration_seconds, 20)
  })
}

test('a failed obsolete route request does not add an error after a successful refresh', async (t) => {
  const requests = []
  const state = await mountView(t, 'ShippingView', {
    fetchDeliveryRoute: () => {
      const request = deferred()
      requests.push(request)
      return request.promise
    },
  })
  const first = state.planRoute({ id: 1 })
  const second = state.planRoute({ id: 1 })
  requests[1].resolve({ order_id: 1 })
  await second
  requests[0].reject(new Error('obsolete route failure'))
  await first
  assert.equal(state.routeError, '')
  assert.equal(state.selectedRoute.order_id, 1)
})

function shippingOrder(id = 1, overrides = {}) {
  return { id, order_number: `ORD-${id}`, status: 'paid_pending_shipment',
    items: [{ id: 1, product_name: '测试商品', quantity: 2 }], shipment: null,
    longitude: null, latitude: null, ...overrides }
}
function shipmentRecord(overrides = {}) {
  return { order_id: 1, carrier_name: '顺丰速运', tracking_number: 'SF123456789000',
    remark: '', revision: 1, historical: false, ...overrides }
}

test('shipping can record a waybill without coordinates and saving never dispatches stock', async (t) => {
  let writes = 0
  let dispatches = 0
  const state = await mountView(t, 'ShippingView', {
    fetchOrder: async () => shippingOrder(),
    saveShipment: async (id, payload) => { writes++; assert.equal(payload.expected_revision, 0); return shipmentRecord() },
    dispatchShipment: async () => { dispatches++ },
  })
  await state.openOrder(1)
  assert.equal(state.canDispatch, false)
  state.draft.carrier_name = '顺丰速运'
  state.draft.tracking_number = 'SF123456789000'
  await state.recordShipment()
  assert.equal(writes, 1)
  assert.equal(dispatches, 0)
  assert.equal(state.selectedOrder.status, 'paid_pending_shipment')
  assert.equal(state.selectedOrder.longitude, null)
  assert.equal(state.canDispatch, true)
  state.draft.tracking_number = 'SF987654321000'
  assert.equal(state.canDispatch, false)
})

test('shipping route failures do not disable handover of a saved waybill', async (t) => {
  const state = await mountView(t, 'ShippingView', {
    fetchOrder: async () => shippingOrder(1, { shipment: shipmentRecord() }),
    fetchDeliveryRoute: async () => { throw new Error('地图服务暂不可用') },
  })
  await state.openOrder(1)
  await state.planRoute({ id: 1 })
  assert.match(state.routeError, /地图服务暂不可用/)
  assert.equal(state.canDispatch, true)
  assert.equal(state.selectedOrder.longitude, null)
})

test('shipping rejects invalid waybills before sending a mutation', async (t) => {
  let writes = 0
  const state = await mountView(t, 'ShippingView', {
    fetchOrder: async () => shippingOrder(), saveShipment: async () => { writes++ },
  })
  await state.openOrder(1)
  state.draft.carrier_name = '顺丰速运'
  state.draft.tracking_number = 'SF 1234'
  await state.recordShipment()
  assert.equal(writes, 0)
  assert.match(state.detailError, /运单号/)
})

test('shipping handover retries reuse a key and preserve the saved waybill version', async (t) => {
  const calls = []
  const state = await mountView(t, 'ShippingView', {
    fetchOrder: async () => shippingOrder(1, { shipment: shipmentRecord({ revision: 3 }) }),
    dispatchShipment: async (...args) => {
      calls.push(args)
      if (calls.length === 1) throw new Error('网络暂时不可用')
      return shippingOrder(1, { status: 'in_delivery', shipment: shipmentRecord({ revision: 3, dispatched_at: '2026-10-29T01:00:00Z' }) })
    },
  })
  await state.openOrder(1)
  await state.confirmDispatch()
  assert.equal(state.canDispatch, true)
  await state.confirmDispatch()
  assert.equal(calls.length, 2)
  assert.equal(calls[0][1], 3)
  assert.equal(calls[0][2], calls[1][2])
  assert.equal(state.selectedOrder.status, 'in_delivery')
  assert.equal(state.editable, false)
  assert.equal(state.canDispatch, false)
})

test('shipping discards stale order details after another order is selected', async (t) => {
  const pending = deferred()
  const state = await mountView(t, 'ShippingView', {
    fetchOrder: (id) => id === 1 ? pending.promise : Promise.resolve(shippingOrder(2)),
  })
  const first = state.openOrder(1)
  await state.openOrder(2)
  pending.resolve(shippingOrder(1))
  await first
  assert.equal(state.selectedOrder.id, 2)
  assert.equal(state.selectedId, 2)
})

test('shipping ignores an outdated list result and forwards pagination and filters', async (t) => {
  const requests = []
  const state = await mountView(t, 'ShippingView', {
    fetchShippingWorkbench: (params) => { const request = deferred(); requests.push({ params, ...request }); return request.promise },
  })
  state.keywordDraft = 'SF123456789000'
  state.applySearch()
  requests[1].resolve({ items: [shippingOrder(2)], total: 1, counts: { paid_pending_shipment: 1 } })
  await settle()
  requests[0].resolve({ items: [shippingOrder(1)], total: 1, counts: {} })
  await settle()
  assert.equal(state.rows[0].id, 2)
  assert.equal(requests[1].params.keyword, 'SF123456789000')
  assert.equal(requests[1].params.status, 'paid_pending_shipment')
  assert.equal(requests[1].params.page_size, 20)
})

test('shipping deep links load an order independently of the visible list page', async (t) => {
  const state = await mountView(t, 'ShippingView', {
    __window: { location: { search: '?status=in_delivery&order_id=101' } },
    fetchOrder: async (id) => shippingOrder(id, { status: 'in_delivery' }),
  })
  assert.equal(state.rows.length, 0)
  assert.equal(state.selectedOrder.id, 101)
  assert.equal(state.tab, 'in_delivery')
  const link = new URL(state.orderLink(state.selectedOrder), 'http://app.test')
  assert.equal(link.searchParams.get('order_id'), '101')
  assert.equal(link.searchParams.get('keyword'), 'ORD-101')
})

test('historical waybill backfill requires a source and becomes read-only after saving', async (t) => {
  let writes = 0
  const state = await mountView(t, 'ShippingView', {
    fetchOrder: async () => shippingOrder(1, { status: 'in_delivery' }),
    saveShipment: async () => { writes++; return shipmentRecord({ historical: true, remark: '纸质记录' }) },
  })
  await state.openOrder(1)
  state.draft.carrier_name = '顺丰速运'
  state.draft.tracking_number = 'SF123456789000'
  await state.recordShipment()
  assert.equal(writes, 0)
  assert.match(state.detailError, /信息来源/)
  state.draft.remark = '纸质记录'
  await state.recordShipment()
  assert.equal(writes, 1)
  assert.equal(state.editable, false)
  assert.equal(state.canDispatch, false)
})

test('pagination reaches order 101 and changing the status filter returns to page one', async (t) => {
  const records = Array.from({ length: 101 }, (_, index) => ({ id: index + 1 }))
  const calls = []
  const state = await mountView(t, 'OrdersView', {
    fetchOrders: async (status, page, pageSize) => {
      calls.push({ status, page, pageSize })
      return { items: records.slice((page - 1) * pageSize, page * pageSize), total: records.length }
    },
  })
  assert.equal(state.total, 101)
  assert.equal(state.pageCount, 6)
  await state.changePage(6)
  assert.equal(state.orders.length, 1)
  assert.equal(state.orders[0].id, 101)
  state.filter = 'pending_payment'
  await settle()
  assert.equal(state.page, 1)
  assert.equal(calls.at(-1).status, 'pending_payment')
  assert.equal(calls.at(-1).page, 1)
})

test('an empty last page moves back after the result count shrinks', async (t) => {
  let records = Array.from({ length: 21 }, (_, index) => ({ id: index + 1 }))
  const state = await mountView(t, 'OrdersView', {
    fetchOrders: async (_status, page, pageSize) => ({
      items: records.slice((page - 1) * pageSize, page * pageSize),
      total: records.length,
    }),
  })
  await state.changePage(2)
  records = records.slice(0, 20)
  await state.loadOrders()
  assert.equal(state.page, 1)
  assert.equal(state.orders.length, 20)
  assert.equal(state.loading, false)
})

test('an old page response cannot overwrite a newer filter result', async (t) => {
  const requests = []
  let delayed = false
  const state = await mountView(t, 'OrdersView', {
    fetchOrders: async () => {
      if (!delayed) return { items: [{ id: 1 }], total: 40 }
      const request = deferred()
      requests.push(request)
      return request.promise
    },
  })
  delayed = true
  const oldPage = state.changePage(2)
  state.filter = 'cancelled'
  await Vue.nextTick()
  requests[1].resolve({ items: [{ id: 3 }], total: 1 })
  await settle()
  requests[0].resolve({ items: [{ id: 2 }], total: 40 })
  await oldPage
  assert.equal(state.orders[0].id, 3)
  assert.equal(state.total, 1)
  assert.equal(state.page, 1)
})

test('the order form keeps the address of the latest selected customer', async (t) => {
  const requests = []
  const state = await mountView(t, 'OrdersView', {
    fetchCustomerAddresses: () => {
      const request = deferred()
      requests.push(request)
      return request.promise
    },
  })
  const first = state.changeCustomer({ target: { value: '1' } })
  const second = state.changeCustomer({ target: { value: '2' } })
  requests[1].resolve([{ id: 22, customer_id: 2, is_default: true }])
  await second
  requests[0].resolve([])
  await first
  assert.equal(state.selectedAddressId, 22)
  assert.equal(state.addresses[0].customer_id, 2)
  assert.equal(state.dialogError, '')
})

function productDetailFixture() {
  return { id: 3, name: '规格测试商品', price: 0.10, stock: 6, status: 'Active', revision: 1,
    images: [{ id: 1, url: '/red.png', color: 'Red', alt: '' }, { id: 2, url: '/blue.png', color: 'Blue', alt: '' }],
    media: [], description: [], parameters: [], options: { colors: ['Red', 'Blue'], sizes: ['M', 'L'] },
    skus: [{ id: 31, product_id: 3, color: 'Red', size: 'M', price: '0.10', active: true, stock: 5, available_quantity: 5, reserved_quantity: 0 },
      { id: 32, product_id: 3, color: 'Blue', size: 'L', price: '12.30', active: true, stock: 1, available_quantity: 1, reserved_quantity: 0 }] }
}

test('product detail selection switches color images, price, quantity and order SKU', async (t) => {
  const state = await mountView(t, 'ProductDetailView', {
    __window: { location: { search: '?id=3&return=%2Fproducts%3Fpage%3D2' } },
    fetchProductDetail: async () => productDetailFixture(),
  })
  state.quantity = 3
  assert.equal(state.selectionTotal, '0.30')
  assert.equal(state.backUrl, '/products?page=2')
  state.selectColor('Blue')
  await settle()
  assert.equal(state.size, 'L')
  assert.equal(state.currentImage, 1)
  assert.equal(state.selectedSKU.id, 32)
  assert.equal(state.quantity, 1)
  assert.equal(state.selectionTotal, '12.30')
  assert.match(state.orderUrl, /create_sku_id=32/)
  state.stepImage(1)
  assert.equal(state.currentImage, 0)
})

test('product detail ignores an unmounted request and rejects external return URLs', async (t) => {
  const pending = deferred()
  const component = compileComponent('../src/views/ProductDetailView.vue', {
    ...commerceModule(),
    __window: { location: { search: '?id=3&return=https%3A%2F%2Fexample.com' } },
    fetchProductDetail: () => pending.promise,
  })
  component.render = () => null
  const app = renderer.createApp(component, { username: 'test' })
  app.mount({})
  await settle()
  const state = app._instance.setupState
  assert.equal(state.backUrl, '/products')
  app.unmount()
  pending.resolve(productDetailFixture())
  await settle()
  assert.equal(state.detail, null)
})

test('product details preserve the favorites return target but reject untrusted variants', async t => {
  for (const [returnTo, expected] of [['/favorites', '/favorites'], ['/favorites?sort=recent', '/favorites?sort=recent'], ['//example.com', '/products'], ['/favorites/../orders', '/products']]) {
    const state = await mountView(t, 'ProductDetailView', {
      __window: { location: { search: `?id=3&return=${encodeURIComponent(returnTo)}` } },
      fetchProductDetail: async () => productDetailFixture(),
    })
    assert.equal(state.backUrl, expected)
  }
})

test('order creation totals and limits use the selected SKU rather than product totals', async (t) => {
  const state = await mountView(t, 'OrdersView', workbenchServices({ fetchProductDetail: async () => productDetailFixture() }))
  state.products = [{ id: 3, price: 0.10, stock: 6 }]
  state.selectedCustomerId = 1
  state.addresses = [{ id: 1, customer_id: 1 }]
  state.selectedAddressId = 1
  state.lineItems = [{ product_id: 3, quantity: 2 }]
  await state.chooseLineProduct(state.lineItems[0], 32)
  assert.equal(state.lineItems[0].sku_id, 32)
  assert.equal(state.canSubmit, false)
  state.lineItems[0].quantity = 1
  assert.equal(state.canSubmit, true)
  assert.equal(state.subtotal, '12.30')
})

test('an obsolete SKU lookup cannot change a line after another product is selected', async (t) => {
  const pending = deferred()
  const state = await mountView(t, 'OrdersView', workbenchServices({ fetchProductDetail: () => pending.promise }))
  const line = { product_id: 3, quantity: 1 }
  const lookup = state.chooseLineProduct(line, 32)
  line.product_id = 4
  pending.resolve(productDetailFixture())
  await lookup
  assert.equal(line.sku_id, null)
})

function workbenchWindow(search = '') {
  const calls = []
  const location = { search }
  const saved = new Map()
  return {
    calls,
    saved,
    location,
    localStorage: {
      getItem: (key) => saved.get(key) ?? null,
      setItem: (key, value) => saved.set(key, value),
    },
    history: Object.fromEntries(
      ['pushState', 'replaceState'].map((method) => [
        method,
        (_state, _title, url) => {
          calls.push({ method, url })
          location.search = url.includes('?') ? url.slice(url.indexOf('?')) : ''
        },
      ]),
    ),
  }
}

function workbenchServices(overrides = {}) {
  const rows = Array.from({ length: 21 }, (_, index) => ({
    id: index + 1,
    status: 'pending_payment',
    print_request_count: 0,
  }))
  return {
    fetchOrders: async (_status, page, size) => ({
      items: rows.slice((page - 1) * size, page * size),
      total: rows.length,
    }),
    fetchOrder: async (id) => rows.find((row) => row.id === id),
    request: async () => ({ items: [], total: 0 }),
    ...overrides,
  }
}

test('order page and details push navigable URLs and restoring a detail URL reopens it', async (t) => {
  const browser = workbenchWindow('?keyword=快照&time_field=paid_at')
  const state = await mountView(t, 'OrdersView', workbenchServices({ __window: browser }))
  await state.changePage(2)
  assert(browser.calls.some((call) => call.method === 'pushState' && call.url.includes('page=2')))
  await state.openOrder(state.orders[0])
  assert.equal(new URLSearchParams(browser.location.search).get('order_id'), '21')
  assert.equal(state.selectedOrder.id, 21)
  state.closeOrder()
  assert.equal(new URLSearchParams(browser.location.search).has('order_id'), false)
  assert.equal(state.page, 2)
  browser.location.search = '?page=2&keyword=快照&time_field=paid_at&order_id=21'
  state.restoreFilters()
  await settle()
  assert.equal(state.selectedOrder.id, 21)
  assert.equal(state.filters.time_field, 'paid_at')
  assert.equal(state.filters.keyword, '快照')
})

test('creating an order from customer details loads the customer and default address', async (t) => {
  const state = await mountView(t, 'OrdersView', workbenchServices({
    __window: workbenchWindow('?create_customer_id=7'),
    fetchCustomers: async () => [{ id: 7, name: '客户七' }],
    fetchCustomerAddresses: async id => [{ id: 70, customer_id: id, is_default: true }],
    fetchOrderableProducts: async () => [{ id: 1, stock: 10, price: 5 }],
  }))
  assert.equal(state.dialogOpen, true)
  assert.equal(state.selectedCustomerId, 7)
  assert.equal(state.selectedAddressId, 70)
  assert.equal(state.dialogError, '')
})

test('clicking the same order toggles details and preserves page and filters', async (t) => {
  const browser = workbenchWindow('?keyword=快照&time_field=paid_at')
  let reads = 0
  const state = await mountView(t, 'OrdersView', workbenchServices({
    __window: browser,
    fetchOrder: async (id) => { reads++; return { id } },
  }))
  await state.changePage(2)
  const event = { button: 0, preventDefault() {} }
  await state.clickOrder(event, state.orders[0])
  assert.equal(state.selectedOrder.id, 21)
  await state.clickOrder(event, state.orders[0])
  assert.equal(state.selectedOrder, null)
  assert.equal(state.detailOrderId, null)
  assert.equal(reads, 1)
  const params = new URLSearchParams(browser.location.search)
  assert.equal(params.has('order_id'), false)
  assert.equal(params.get('page'), '2')
  assert.equal(params.get('keyword'), '快照')
  assert.equal(params.get('time_field'), 'paid_at')
})

test('collapsing a loading order discards its late detail response', async (t) => {
  const pending = deferred()
  const state = await mountView(t, 'OrdersView', workbenchServices({ fetchOrder: () => pending.promise }))
  const event = { button: 0, preventDefault() {} }
  const opening = state.clickOrder(event, state.orders[0])
  assert.equal(state.detailLoading, true)
  await state.clickOrder(event, state.orders[0])
  pending.resolve({ id: 1 })
  await opening
  assert.equal(state.detailOrderId, null)
  assert.equal(state.selectedOrder, null)
  assert.equal(state.detailLoading, false)
})

test('switching order headers hides the old detail and ignores a stale response', async (t) => {
  const pending = deferred()
  const state = await mountView(t, 'OrdersView', workbenchServices({
    fetchOrder: async (id) => id === 2 ? pending.promise : { id },
  }))
  const event = { button: 0, preventDefault() {} }
  await state.clickOrder(event, state.orders[0])
  const second = state.clickOrder(event, state.orders[1])
  assert.equal(state.selectedOrder, null)
  assert.equal(state.detailOrderId, 2)
  await state.clickOrder(event, state.orders[2])
  pending.resolve({ id: 2 })
  await second
  assert.equal(state.selectedOrder.id, 3)
  assert.equal(state.detailOrderId, 3)
})

test('modified order clicks retain native link navigation', async (t) => {
  const state = await mountView(t, 'OrdersView', workbenchServices())
  for (const modifier of [{ ctrlKey: true }, { metaKey: true }, { shiftKey: true }, { button: 1 }]) {
    await state.clickOrder({ button: 0, preventDefault() { assert.fail('native navigation was prevented') }, ...modifier }, state.orders[0])
  }
  assert.equal(state.detailOrderId, null)
})

test('order detail retry reloads a failed detail without changing navigation', async (t) => {
  let reads = 0
  const browser = workbenchWindow()
  const state = await mountView(t, 'OrdersView', workbenchServices({
    __window: browser,
    fetchOrder: async (id) => { if (++reads === 1) throw new Error('详情读取失败'); return { id } },
  }))
  await state.openOrder(state.orders[0])
  assert.equal(state.selectedOrder, null)
  assert.match(state.detailError, /详情读取失败/)
  const historyCount = browser.calls.length
  await state.retryOrderDetails()
  assert.equal(state.selectedOrder.id, 1)
  assert.equal(state.detailError, '')
  assert.equal(browser.calls.length, historyCount)
})

test('retrying evidence keeps the loaded order and an unsaved internal note', async (t) => {
  let reads = 0
  let fail = true
  const state = await mountView(t, 'OrdersView', workbenchServices({
    fetchOrder: async (id) => { reads++; return { id } },
    request: async () => { if (fail) throw new Error('依据读取失败'); return { items: [], total: 0 } },
  }))
  await state.openOrder(state.orders[0])
  state.internalNote = '尚未提交的备注'
  fail = false
  await state.retryOrderDetails()
  assert.equal(reads, 1)
  assert.equal(state.detailError, '')
  assert.equal(state.internalNote, '尚未提交的备注')
})

test('invalid order amounts and reversed dates keep the list and identify the field', async (t) => {
  let loads = 0
  const state = await mountView(
    t,
    'OrdersView',
    workbenchServices({
      fetchOrders: async () => {
        loads++
        return { items: [{ id: 1 }], total: 1 }
      },
    }),
  )
  state.filters.min_total = '30.00'
  state.filters.max_total = '20.00'
  state.applyFilters()
  assert.match(state.error, /最低应收/)
  assert.equal(loads, 1)
  assert.equal(state.orders[0].id, 1)
  state.filters.min_total = '1.005'
  state.applyFilters()
  assert.match(state.error, /两位小数/)
  state.filters.min_total = ''
  state.filters.max_total = ''
  state.filters.start_at = '2026-10-02T00:00:00+08:00'
  state.filters.end_at = '2026-10-01T00:00:00+08:00'
  state.applyFilters()
  assert.match(state.error, /开始时间/)
  assert.equal(loads, 1)
})

test('unknown-time selection clears date and snapshot constraints', async (t) => {
  const state = await mountView(t, 'OrdersView', workbenchServices())
  state.filters = {
    time_field: 'paid_at',
    start_at: '2026-10-01T00:00:00Z',
    end_at: '2026-10-02T00:00:00Z',
    as_of: '2026-10-02T00:00:00Z',
    unknown_time: true,
  }
  state.changeUnknownTime()
  assert.equal(state.filters.start_at, '')
  assert.equal(state.filters.end_at, '')
  assert.equal(state.filters.as_of, '')
  assert.equal(state.filters.time_field, 'paid_at')
})

test('saved order filters preserve statuses but exclude page, detail and snapshot boundary', async (t) => {
  const browser = workbenchWindow('?page=2&order_id=21&as_of=2026-10-28T00%3A00%3A00Z')
  const state = await mountView(t, 'OrdersView', workbenchServices({ __window: browser }))
  state.filter = 'all'
  state.filters.statuses = ['pending_payment', 'paid_pending_shipment']
  state.keywordDraft = ' 原快照 '
  state.presetName = '我的待办'
  state.savePreset()
  const saved = JSON.parse(browser.saved.get('orders:filters:test'))
  const params = new URLSearchParams(saved[0].params)
  assert.deepEqual(params.getAll('statuses'), ['pending_payment', 'paid_pending_shipment'])
  assert.equal(params.get('keyword'), '原快照')
  for (const key of ['page', 'order_id', 'as_of']) assert.equal(params.has(key), false)
  state.usePreset(saved[0].params)
  await settle()
  assert.equal(state.page, 1)
  assert.equal(state.filters.keyword, '原快照')
  state.removePreset('我的待办')
  assert.equal(state.presets.length, 0)
})

test('changing pages clears selection with a visible notice', async (t) => {
  const state = await mountView(t, 'OrdersView', workbenchServices())
  state.toggleSelection(1)
  await state.changePage(2)
  assert.equal(state.selectedIds.length, 0)
  assert.match(state.selectionNotice, /已清空/)
})

test('print conflicts retain selection and do not refresh or silently omit orders', async (t) => {
  let closed = false
  const browser = workbenchWindow()
  browser.open = () => ({
    document: { body: {} },
    closed: false,
    close: () => {
      closed = true
    },
  })
  const state = await mountView(
    t,
    'OrdersView',
    workbenchServices({
      __window: browser,
      request: async (_path, method) => {
        if (method === 'POST') throw new Error('整批未生成：ORD-2 已取消')
        return { items: [], total: 0 }
      },
    }),
  )
  state.toggleSelection(1)
  state.toggleSelection(2)
  state.openPrint(state.orders.slice(0, 2))
  await state.submitPrint()
  assert.equal(state.selectedIds.length, 2)
  assert.match(state.printError, /ORD-2 已取消/)
  assert.equal(state.printOpen, true)
  assert.equal(closed, true)
})

test('note retries share a key but a new deliberate identical note gets a new key', async (t) => {
  const keys = []
  let serial = 0
  const state = await mountView(
    t,
    'OrdersView',
    workbenchServices({
      __crypto: { randomUUID: () => `unique-intent-${++serial}` },
      request: async (_path, method, payload) => {
        if (method === 'POST') keys.push(payload.idempotency_key)
        return { items: [], total: 0 }
      },
    }),
  )
  await state.openOrder(state.orders[0])
  state.internalNote = '已核对'
  await state.appendNote()
  state.internalNote = '已核对'
  await state.appendNote()
  assert.equal(keys.length, 2)
  assert.notEqual(keys[0], keys[1])
})

test('order CSV uses applied URL conditions and excludes pagination and open detail', async (t) => {
  const browser = workbenchWindow('?keyword=快照&statuses=pending_payment&page=2&order_id=21')
  let downloaded
  const state = await mountView(
    t,
    'OrdersView',
    workbenchServices({
      __window: browser,
      downloadCsv: async (path) => {
        downloaded = path
      },
    }),
  )
  await state.exportOrders()
  const params = new URLSearchParams(downloaded.split('?')[1])
  assert.equal(params.get('keyword'), '快照')
  assert.equal(params.get('statuses'), 'pending_payment')
  for (const key of ['page', 'page_size', 'order_id']) assert.equal(params.has(key), false)
})

test('orders API forwards the requested page and page size', async () => {
  let requestedUrl
  let apiError = false
  const api = loadApiModule('features/orders/api/ordersApi.ts', {
    fetch: async (url) => {
      requestedUrl = url
      if (apiError)
        return {
          ok: false,
          status: 422,
          json: async () => ({ detail: '最低金额不能高于最高金额' }),
        }
      return { ok: true, json: async () => ({ items: [] }) }
    },
  })
  await api.fetchOrders('cancelled', 6, 20)
  const url = new URL(requestedUrl)
  assert.equal(url.searchParams.get('page'), '6')
  assert.equal(url.searchParams.get('page_size'), '20')
  assert.equal(url.searchParams.get('status'), 'cancelled')
  apiError = true
  await assert.rejects(api.fetchOrders(), /最低金额不能高于最高金额/)
})

async function mountCachedMap(t, { settingsPromise, sdkPromise } = {}) {
  const visible = Vue.ref(true)
  const instances = []
  const calls = { settings: 0, sdk: 0, destroy: 0, centers: [] }
  const module = {
    Map: class {
      constructor(element, options) {
        instances.push({ element, options })
      }
      clearMap() { }
      setZoomAndCenter(zoom, center) { calls.centers.push({ zoom, center }) }
      destroy() {
        calls.destroy++
      }
    },
  }
  const component = compileComponent('../src/features/shipping/components/MapContainer.vue', {
    useI18n: () => ({ t: (key) => key }),
    getAmapConfig: () => ({ key: 'test-key', serviceHost: 'http://127.0.0.1:8080/_AMapService' }),
    fetchShippingMapSettings: () => {
      calls.settings++
      return settingsPromise ?? Promise.resolve({ center: [114, 38], zoom: 12 })
    },
    loadAmapApi: () => {
      calls.sdk++
      return sdkPromise ?? Promise.resolve(module)
    },
  })
  let state
  component.render = () => {
    state = Vue.getCurrentInstance().setupState
    return Vue.h('div', {
      ref: (element) => {
        state.mapElement = element
      },
    })
  }
  const app = renderer.createApp({
    setup: () => () =>
      Vue.h(
        Vue.KeepAlive,
        { include: 'MapContainer' },
        {
          default: () => (visible.value ? Vue.h(component) : Vue.h('span')),
        },
      ),
  })
  app.mount({})
  let unmounted = false
  const unmount = () => {
    if (unmounted) return
    unmounted = true
    app.unmount()
  }
  t.after(unmount)
  await settle()
  return { visible, instances, calls, module, state, unmount }
}

test('map settings and the SDK start together; delivery uses the lighter 2D view', async (t) => {
  const settings = deferred()
  const sdk = deferred()
  const map = await mountCachedMap(t, {
    settingsPromise: settings.promise,
    sdkPromise: sdk.promise,
  })
  assert.equal(map.calls.settings, 1)
  assert.equal(map.calls.sdk, 1)
  assert.equal(map.instances.length, 0)
  sdk.resolve(map.module)
  settings.resolve({ center: [114, 38], zoom: 12 })
  await settle()
  assert.equal(map.instances.length, 1)
  assert.equal(map.instances[0].options.viewMode, '2D')
  assert.equal(map.instances[0].options.animateEnable, false)
})

test('returning to a cached map reuses its instance and settings', async (t) => {
  const map = await mountCachedMap(t)
  assert.equal(map.instances.length, 1)
  const original = map.instances[0].element
  map.visible.value = false
  await settle()
  assert.equal(map.calls.destroy, 0)
  map.visible.value = true
  await settle()
  assert.equal(map.instances.length, 1)
  assert.equal(map.instances[0].element, original)
  assert.equal(map.calls.settings, 1)
  assert.equal(map.calls.sdk, 1)
  map.unmount()
  assert.equal(map.calls.destroy, 1)
})

test('a slow map settings response does not block the base map', async (t) => {
  const settings = deferred()
  const map = await mountCachedMap(t, { settingsPromise: settings.promise })
  assert.equal(map.instances.length, 1)
  assert.equal(map.instances[0].options.zoom, 5)
  assert.equal(map.state.loading, false)
  settings.resolve({ center: [114, 38], zoom: 12 })
  await settle()
  assert.equal(map.instances.length, 1)
  assert.equal(map.calls.centers.length, 1)
  assert.equal(map.calls.centers[0].zoom, 12)
})

test('failed map settings leave the base map usable', async (t) => {
  const settings = deferred()
  const map = await mountCachedMap(t, { settingsPromise: settings.promise })
  settings.reject(new Error('settings unavailable'))
  await settle()
  assert.equal(map.instances.length, 1)
  assert.equal(map.state.errorMessage, '')
  assert.equal(map.state.loading, false)
})

test('late settings do not touch a map that has been destroyed', async (t) => {
  const settings = deferred()
  const map = await mountCachedMap(t, { settingsPromise: settings.promise })
  map.unmount()
  settings.resolve({ center: [114, 38], zoom: 12 })
  await settle()
  assert.equal(map.calls.destroy, 1)
  assert.equal(map.calls.centers.length, 0)
})

test('shipping preloads the SDK before the map is opened', async (t) => {
  let loads = 0
  const state = await mountView(t, 'ShippingView', {
    loadAmapApi: async () => { loads++ },
  })
  assert.equal(state.mapOpen, false)
  assert.equal(loads, 1)
})

test('a slow SDK does not create a map after the page has been unmounted', async (t) => {
  const sdk = deferred()
  const map = await mountCachedMap(t, { sdkPromise: sdk.promise })
  map.unmount()
  sdk.resolve(map.module)
  await settle()
  assert.equal(map.instances.length, 0)
})

test('a map that finishes loading while hidden initializes only after activation', async (t) => {
  const sdk = deferred()
  const map = await mountCachedMap(t, { sdkPromise: sdk.promise })
  map.visible.value = false
  await settle()
  sdk.resolve(map.module)
  await settle()
  assert.equal(map.instances.length, 0)
  map.visible.value = true
  await settle()
  assert.equal(map.instances.length, 1)
})

test('a cached delivery page refreshes its queue when activated again', async (t) => {
  let requests = 0
  const visible = Vue.ref(true)
  const component = compileComponent('../src/views/ShippingView.vue', {
    useI18n: () => ({ t: (key) => key }),
    fetchShippingWorkbench: async () => {
      requests++
      return { items: [], total: 0, counts: {} }
    },
  })
  component.render = () => null
  const app = renderer.createApp({
    setup: () => () =>
      Vue.h(
        Vue.KeepAlive,
        { include: 'ShippingView' },
        {
          default: () => (visible.value ? Vue.h(component, { username: 'test' }) : Vue.h('span')),
        },
      ),
  })
  app.mount({})
  t.after(() => app.unmount())
  await settle()
  assert.equal(requests, 1)
  visible.value = false
  await settle()
  visible.value = true
  await settle()
  assert.equal(requests, 2)
})
async function mountShippingFeature(t, name, services, props = {}) {
  const component = compileComponent(`../src/features/shipping/components/${name}.vue`, {
    ...commerceModule(), ...services,
  })
  component.render = () => null
  const app = renderer.createApp(component, { orderId: 7, ...props })
  app.mount({})
  let mounted = true
  const unmount = () => { if (mounted) { app.unmount(); mounted = false } }
  t.after(unmount)
  await settle()
  return { state: app._instance.setupState, unmount }
}
const trackingFixture = (overrides = {}) => ({
  order_id: 7, service: { provider_name: '快递100', configured: true, interval_minutes: 30 },
  eligible: true, status: 'in_transit', status_name: '运输中', events: [{ id: 1, description: '测试记录' }],
  last_success_at: '2026-10-29T01:00:00Z', ...overrides,
})

const packageFixture = (overrides = {}) => ({
  order_id: 7, revision: 1, actual_cost: '0.00', unsettled_packages: 1, label_tasks: [],
  items: [{ package_no: 1, carrier_name: '顺丰速运', tracking_number: 'SF123456789000', remark: '', freight: null, label: null }],
  ...overrides,
})

test('parcel label is blocked by missing credentials and unresolved tasks', async (t) => {
  let writes = 0
  const { state } = await mountShippingFeature(t, 'ParcelPanel', {
    fetchPackages: async () => packageFixture({ items: [], revision: 0 }),
    request: async (_path, method) => { if (method === 'POST') writes++; return { configured: false } },
  }, { order: shippingOrder(7) })
  await state.takeLabel()
  assert.equal(writes, 0)
  state.configured = true
  state.data = packageFixture({ items: [], revision: 0, label_tasks: [{ status: 'unknown' }] })
  await state.takeLabel()
  assert.equal(writes, 0)
  assert.equal(state.editable, false)
})

test('uncertain label transport retries retain one idempotency key', async (t) => {
  const calls = []
  let serial = 0
  const { state } = await mountShippingFeature(t, 'ParcelPanel', {
    __crypto: { randomUUID: () => `label-intent-${++serial}` },
    fetchPackages: async () => packageFixture({ items: [], revision: 0 }),
    request: async (_path, method, body) => {
      if (method === 'POST') { calls.push(body); throw new Error('网络断开') }
      return { configured: true }
    },
  }, { order: shippingOrder(7) })
  await state.takeLabel()
  await state.takeLabel()
  assert.equal(calls.length, 2)
  assert.equal(calls[0].idempotency_key, calls[1].idempotency_key)
  assert.equal(calls[0].expected_revision, 0)
  assert.match(state.error, /网络断开/)
})

test('freight corrections submit the displayed revision and preserve zero cost', async (t) => {
  const calls = []
  const fixture = packageFixture()
  fixture.items[0].freight = { actual_cost: '5.20', source: '旧账单', revision: 3 }
  const { state } = await mountShippingFeature(t, 'ParcelPanel', {
    fetchPackages: async () => fixture,
    request: async (path, method, body) => { if (method === 'PUT') calls.push({ path, body }); return { configured: false } },
  }, { order: shippingOrder(7, { status: 'in_delivery', shipment: shipmentRecord({ dispatched_at: '2026-10-29T01:00:00Z' }) }) })
  state.costs[1].cost = '0.00'
  state.costs[1].source = '账单确认免费'
  await state.settle(fixture.items[0])
  assert.equal(calls[0].body.actual_cost, '0.00')
  assert.equal(calls[0].body.expected_revision, 3)
})

test('a late package response cannot fill an unmounted detail', async (t) => {
  const pending = deferred()
  const { state, unmount } = await mountShippingFeature(t, 'ParcelPanel', {
    fetchPackages: () => pending.promise, request: async () => ({ configured: false }),
  }, { order: shippingOrder(7) })
  unmount()
  pending.resolve(packageFixture())
  await settle()
  assert.equal(state.data, null)
})

test('an older package refresh cannot overwrite a newer revision and freight draft', async (t) => {
  const old = deferred()
  let reads = 0
  const { state } = await mountShippingFeature(t, 'ParcelPanel', {
    fetchPackages: () => ++reads === 1 ? old.promise : Promise.resolve(packageFixture({ revision: 2 })),
    request: async () => ({ configured: false }),
  }, { order: shippingOrder(7) })
  await state.load()
  state.costs[1].cost = '8.50'
  state.costs[1].source = '核对后的账单'
  old.resolve(packageFixture({ revision: 1 }))
  await settle()
  assert.equal(state.data.revision, 2)
  assert.equal(state.costs[1].cost, '8.50')
  assert.equal(state.costs[1].source, '核对后的账单')
})

test('an older package refresh failure cannot replace the current successful result', async (t) => {
  const old = deferred()
  let reads = 0
  const { state } = await mountShippingFeature(t, 'ParcelPanel', {
    fetchPackages: () => ++reads === 1 ? old.promise : Promise.resolve(packageFixture({ revision: 2 })),
    request: async () => ({ configured: false }),
  }, { order: shippingOrder(7) })
  state.error = '之前的读取失败'
  await state.load()
  old.reject(new Error('过期请求的错误'))
  await settle()
  assert.equal(state.data.revision, 2)
  assert.equal(state.error, '')
})

test('batch preview rejects changed manifest revisions before dispatch', async (t) => {
  let calls = 0
  const { state } = await mountShippingFeature(t, 'BulkShippingPanel', {
    fetchPackages: async () => packageFixture({ revision: 2 }),
    request: async () => { calls++ },
  }, { orders: [shippingOrder(7, { shipment: shipmentRecord() })] })
  await state.prepare()
  await state.dispatch()
  assert.equal(calls, 0)
  assert.equal(state.preview.length, 0)
  assert.match(state.error, /运单变更/)
})

test('an old batch preview failure cannot add an error after the selection changes', async (t) => {
  const pending = deferred()
  const orders = Vue.reactive([shippingOrder(7)])
  const { state } = await mountShippingFeature(t, 'BulkShippingPanel', {
    fetchPackages: () => pending.promise,
  }, { orders })
  const preparing = state.prepare()
  orders.splice(0, 1, shippingOrder(8))
  await settle()
  pending.reject(new Error('旧订单读取失败'))
  await preparing
  assert.equal(state.preview.length, 0)
  assert.equal(state.error, '')
})

test('a manual waybill failure cannot add results to a different batch', async (t) => {
  const pending = deferred()
  const orders = Vue.reactive([shippingOrder(7)])
  const { state } = await mountShippingFeature(t, 'BulkShippingPanel', {
    fetchPackages: async () => packageFixture({ revision: 0, items: [] }),
    request: async () => ({ configured: false }),
    saveShipment: () => pending.promise,
  }, { orders })
  await state.prepare()
  state.manualDrafts[7].tracking_number = 'SF123456000007'
  const saving = state.saveManualWaybills()
  orders.splice(0, 1, shippingOrder(8))
  await settle()
  pending.reject(new Error('旧批次保存失败'))
  await saving
  assert.equal(state.manualResults.length, 0)
})

test('a completed dispatch does not attach its result to a newly selected batch', async (t) => {
  const pending = deferred()
  const orders = Vue.reactive([shippingOrder(7, { shipment: shipmentRecord() })])
  const { state } = await mountShippingFeature(t, 'BulkShippingPanel', {
    fetchPackages: async () => packageFixture(),
    request: async (_path, method) => method ? pending.promise : { configured: false },
  }, { orders })
  await state.prepare()
  const dispatching = state.dispatch()
  orders.splice(0, 1, shippingOrder(8))
  await settle()
  pending.resolve({ items: [{ order_id: 7, success: true, error: null }] })
  await dispatching
  assert.equal(state.results.length, 0)
  assert.equal(state.preview.length, 0)
})

test('batch label requests stop after changing selection and hide the old response', async (t) => {
  const pending = deferred()
  const orders = Vue.reactive([shippingOrder(7), shippingOrder(8)])
  let writes = 0
  const { state } = await mountShippingFeature(t, 'BulkShippingPanel', {
    fetchPackages: async () => packageFixture({ revision: 0, items: [] }),
    request: async (_path, method) => {
      if (!method) return { configured: true }
      writes++
      return pending.promise
    },
  }, { orders })
  await state.prepare()
  const taking = state.takeLabels()
  orders.splice(0, orders.length, shippingOrder(9))
  await settle()
  pending.resolve({ status: 'issued', error: null })
  await taking
  assert.equal(writes, 1)
  assert.equal(state.labelResults.length, 0)
})

test('batch network retries retain the batch key and prevent overlapping submissions', async (t) => {
  const pending = deferred()
  const calls = []
  let serial = 0
  const { state } = await mountShippingFeature(t, 'BulkShippingPanel', {
    __crypto: { randomUUID: () => `batch-intent-${++serial}` },
    fetchPackages: async () => packageFixture(),
    request: async (_path, _method, body) => {
      if (!_method) return { configured: false }
      calls.push(body)
      if (calls.length === 1) return pending.promise
      return { items: [{ order_id: 7, success: true, error: null }] }
    },
  }, { orders: [shippingOrder(7, { shipment: shipmentRecord() })] })
  await state.prepare()
  const first = state.dispatch()
  await state.dispatch()
  assert.equal(calls.length, 1)
  pending.reject(new Error('网络断开'))
  await first
  await state.dispatch()
  assert.equal(calls.length, 2)
  assert.equal(calls[0].idempotency_key, calls[1].idempotency_key)
  assert.equal(state.preview.length, 0)
  assert.equal(state.results[0].success, true)
})

test('manual batch waybills work without electronic label credentials and dispatch uses saved revisions', async (t) => {
  const saved = new Map()
  const dispatches = []
  const orders = Vue.reactive([shippingOrder(7), shippingOrder(8)])
  const { state } = await mountShippingFeature(t, 'BulkShippingPanel', {
    fetchPackages: async (id) => saved.has(id) ? packageFixture({ order_id: id }) : packageFixture({ order_id: id, items: [], revision: 0 }),
    saveShipment: async (id, body) => { saved.set(id, body) },
    request: async (path, method, body) => {
      if (!method) return { configured: false }
      dispatches.push(body)
      return { items: body.items.map(item => ({ order_id: item.order_id, success: true })) }
    },
  }, { orders })
  await state.prepare()
  assert.equal(state.readyToDispatch, false)
  state.manualDrafts[7].tracking_number = 'SF123456000007'
  state.manualDrafts[8].tracking_number = 'SF123456000008'
  await state.saveManualWaybills()
  assert.equal(saved.size, 2)
  assert.equal(saved.get(7).expected_revision, 0)
  assert.equal(dispatches.length, 0)
  assert.equal(state.readyToDispatch, true)
  assert.equal(state.readyToPrint, false)
  orders[0].shipment = shipmentRecord()
  orders[1].shipment = shipmentRecord()
  await settle()
  assert.equal(state.readyToDispatch, true)
  await state.dispatch()
  assert.deepEqual(Array.from(dispatches[0].items, item => item.expected_revision), [1, 1])
})

test('manual batch waybills validate numbers and reject duplicate assignments before saving', async (t) => {
  let writes = 0
  const { state } = await mountShippingFeature(t, 'BulkShippingPanel', {
    fetchPackages: async () => packageFixture({ items: [], revision: 0 }),
    saveShipment: async () => { writes++ },
    request: async () => ({ configured: false }),
  }, { orders: [shippingOrder(7), shippingOrder(8)] })
  await state.prepare()
  state.manualDrafts[7].tracking_number = '123'
  await state.saveManualWaybills()
  assert.equal(writes, 0)
  assert.match(state.error, /6–80/)
  state.manualDrafts[7].tracking_number = 'SF123456000007'
  state.manualDrafts[8].tracking_number = 'SF123456000007'
  await state.saveManualWaybills()
  assert.equal(writes, 0)
  assert.match(state.error, /不能重复/)
})

test('partial manual save failures keep successful waybills and retry only missing orders', async (t) => {
  const saved = new Set()
  const calls = []
  let fail = true
  const { state } = await mountShippingFeature(t, 'BulkShippingPanel', {
    fetchPackages: async (id) => packageFixture({ order_id: id, items: saved.has(id) ? [{ package_no: 1, carrier_name: '顺丰速运', tracking_number: `SF00000000${id}` }] : [], revision: saved.has(id) ? 1 : 0 }),
    saveShipment: async (id) => { calls.push(id); if (id === 8 && fail) throw new Error('保存失败'); saved.add(id) },
    request: async () => ({ configured: false }),
  }, { orders: [shippingOrder(7), shippingOrder(8)] })
  await state.prepare()
  state.manualDrafts[7].tracking_number = 'SF000000007'
  state.manualDrafts[8].tracking_number = 'SF000000008'
  await state.saveManualWaybills()
  assert.equal(state.readyToDispatch, false)
  assert.equal(state.missingWaybills.length, 1)
  fail = false
  await state.saveManualWaybills()
  assert.deepEqual(calls, [7, 8, 8])
  assert.equal(state.readyToDispatch, true)
})

test('label settings failures do not block manual waybills and failed rechecks discard old previews', async (t) => {
  let failPackages = false
  const { state } = await mountShippingFeature(t, 'BulkShippingPanel', {
    fetchPackages: async () => { if (failPackages) throw new Error('读取失败'); return packageFixture() },
    request: async () => { throw new Error('配置不可用') },
  }, { orders: [shippingOrder(7, { shipment: shipmentRecord() })] })
  await state.prepare()
  assert.equal(state.readyToDispatch, true)
  assert.equal(state.configured, false)
  assert.match(state.settingsError, /人工登记/)
  failPackages = true
  await state.prepare()
  assert.equal(state.readyToDispatch, false)
  assert.equal(state.preview.length, 0)
})

test('mixed batches can print electronic-label orders without requiring paper waybills to have labels', async (t) => {
  const { state } = await mountShippingFeature(t, 'BulkShippingPanel', {
    fetchPackages: async (id) => packageFixture({ order_id: id, items: [{ package_no: 1, label: id === 7 ? { status: 'issued' } : null }] }),
    request: async () => ({ configured: false }),
  }, { orders: [shippingOrder(7, { shipment: shipmentRecord() }), shippingOrder(8, { shipment: shipmentRecord() })] })
  await state.prepare()
  assert.equal(state.readyToDispatch, true)
  assert.equal(state.readyToPrint, true)
  assert.deepEqual(Array.from(state.printableOrders, row => row.order.id), [7])
})

test('freight filters use Shanghai days and include the end date in queries and CSV', async (t) => {
  const downloads = []
  const { state } = await mountShippingFeature(t, 'FreightPanel', {
    request: async () => ({}), downloadCsv: async (...args) => downloads.push(args),
  })
  state.filters.start = '2026-10-01'
  state.filters.end = '2026-10-29'
  state.filters.carrier = '顺丰'
  await state.exportCsv()
  const params = new URLSearchParams(downloads[0][0].split('?')[1])
  assert.equal(params.get('start_at'), '2026-09-30T16:00:00.000Z')
  assert.equal(params.get('end_at'), '2026-10-29T16:00:00.000Z')
  assert.equal(params.get('carrier'), '顺丰')
})

test('batch labels reuse a separate stable intent per order after transport errors', async (t) => {
  const calls = []
  let serial = 0
  const { state } = await mountShippingFeature(t, 'BulkShippingPanel', {
    __crypto: { randomUUID: () => `bulk-label-${++serial}` },
    fetchPackages: async () => packageFixture({ items: [], revision: 0 }),
    request: async (path, method, body) => {
      if (!method) return { configured: true }
      calls.push({ path, body })
      throw new Error('网络中断，结果待核实')
    },
  }, { orders: [shippingOrder(7), shippingOrder(8)] })
  await state.prepare()
  assert.equal(state.readyToDispatch, false)
  assert.equal(state.readyToPrint, false)
  await state.takeLabels()
  await state.takeLabels()
  assert.equal(calls.length, 4)
  assert.notEqual(calls[0].body.idempotency_key, calls[1].body.idempotency_key)
  assert.equal(calls[0].body.idempotency_key, calls[2].body.idempotency_key)
  assert.equal(calls[1].body.idempotency_key, calls[3].body.idempotency_key)
  assert.equal(state.labelResults.length, 2)
})

test('logistics panel never invokes paid query while credentials are missing', async (t) => {
  let calls = 0
  const { state } = await mountShippingFeature(t, 'LogisticsPanel', {
    fetchTracking: async () => trackingFixture({ service: { configured: false }, events: [] }),
    syncTracking: async () => { calls++ },
  })
  await state.synchronize()
  assert.equal(calls, 0)
  assert.equal(state.snapshot.events.length, 0)
})

test('logistics refresh errors preserve the previous successful events', async (t) => {
  const previous = trackingFixture()
  const { state } = await mountShippingFeature(t, 'LogisticsPanel', {
    fetchTracking: async () => previous,
    syncTracking: async () => { throw new Error('平台网络不可用') },
  })
  await state.synchronize()
  assert.equal(state.snapshot.events[0].id, 1)
  assert.equal(state.snapshot.last_success_at, previous.last_success_at)
  assert.match(state.error, /平台网络不可用/)
})

test('logistics sync blocks overlapping clicks and identifies a cached response', async (t) => {
  const pending = deferred()
  let calls = 0
  const { state } = await mountShippingFeature(t, 'LogisticsPanel', {
    fetchTracking: async () => trackingFixture(),
    syncTracking: async () => { calls++; return pending.promise },
  })
  const first = state.synchronize()
  await state.synchronize()
  assert.equal(calls, 1)
  pending.resolve(trackingFixture({ sync_result: 'cached' }))
  await first
  assert.match(state.notice, /查询间隔/)
})

test('an unmounted logistics panel ignores a late request', async (t) => {
  const pending = deferred()
  const { state, unmount } = await mountShippingFeature(t, 'LogisticsPanel', {
    fetchTracking: () => pending.promise,
  })
  unmount()
  pending.resolve(trackingFixture())
  await settle()
  assert.equal(state.snapshot, null)
})

async function mountBusinessFeature(t, feature, name, services, props = {}) {
  const component = compileComponent(`../src/features/${feature}/components/${name}.vue`, {
    ...commerceModule(), ...services,
  })
  component.render = () => null
  const app = renderer.createApp(component, props)
  app.mount({})
  let mounted = true
  const unmount = () => { if (mounted) { app.unmount(); mounted = false } }
  t.after(unmount)
  await settle()
  return { state: app._instance.setupState, unmount }
}
const aftersaleFixture = () => ({ order_id: 7, payment_verified: true, confirmed_paid: '20.00', refunded_amount: '0.00', refundable_amount: '20.00', items: [] })

test('aftersale transport retries reuse the intent and successful new requests get a new key', async (t) => {
  const sent = []
  let calls = 0, keys = 0
  const { state } = await mountBusinessFeature(t, 'aftersales', 'AfterSalesPanel', {
    __crypto: { randomUUID: () => `aftersale-key-${++keys}` },
    fetchAfterSales: async () => aftersaleFixture(),
    createAfterSale: async (_id, payload) => { sent.push(payload); if (++calls === 1) throw new Error('response lost'); return { id: calls } },
  }, { order: { id: 7, items: [] } })
  state.draft.amount = '5.00'
  state.draft.reason = '客户申请'
  await state.create()
  assert.match(state.error, /response lost/)
  await state.create()
  assert.equal(sent[0].idempotency_key, sent[1].idempotency_key)
  await state.create()
  assert.notEqual(sent[1].idempotency_key, sent[2].idempotency_key)
})

test('refund submission freezes displayed revision and blocks overlapping clicks', async (t) => {
  const pending = deferred()
  const writes = []
  const { state } = await mountBusinessFeature(t, 'aftersales', 'AfterSalesPanel', {
    fetchAfterSales: async () => aftersaleFixture(),
    writeRefund: async (_id, _caseId, payload) => { writes.push(payload); return pending.promise },
  }, { order: { id: 7, items: [] } })
  state.openRefund({ id: 9, revision: 4 })
  state.refund.amount = '5.00'
  state.refund.reference = 'verified-bank-reference'
  const first = state.recordRefund()
  await state.recordRefund()
  assert.equal(writes.length, 1)
  assert.equal(writes[0].expected_revision, 4)
  assert.match(writes[0].occurred_at, /Z$/)
  pending.resolve({ id: 9 })
  await first
})

test('aftersale inspection submits accepted and damaged counts separately', async (t) => {
  let body
  const { state } = await mountBusinessFeature(t, 'aftersales', 'AfterSalesPanel', {
    fetchAfterSales: async () => aftersaleFixture(),
    inspectReturn: async (_id, _caseId, payload) => { body = payload; return {} },
  }, { order: { id: 7, items: [] } })
  state.openInspection({ id: 9, revision: 3, items: [{ order_item_id: 8, quantity: 2 }] })
  state.inspection[8].accepted = 1
  state.inspection[8].damaged = 1
  state.inspectionNote = '一件合格一件破损'
  await state.receive()
  assert.equal(body.expected_revision, 3)
  assert.deepEqual(JSON.parse(JSON.stringify(body.items)), [{ order_item_id: 8, accepted_quantity: 1, damaged_quantity: 1 }])
})

test('late aftersale response cannot repopulate an unmounted order panel', async (t) => {
  const pending = deferred()
  const { state, unmount } = await mountBusinessFeature(t, 'aftersales', 'AfterSalesPanel', {
    fetchAfterSales: async () => pending.promise,
  }, { order: { id: 7, items: [] } })
  unmount()
  pending.resolve(aftersaleFixture())
  await settle()
  assert.equal(state.snapshot, null)
})

test('inventory adjustment rejects blank counts and sends actual on-hand separately from deltas', async (t) => {
  const writes = []
  const { state } = await mountBusinessFeature(t, 'inventory', 'AdjustmentPanel', {
    fetchAdjustments: async () => ({ items: [], total: 0 }),
    createAdjustment: async payload => { writes.push(payload); return {} },
  })
  state.lines[0].productId = '7'
  await state.save()
  assert.equal(writes.length, 0)
  assert.match(state.error, /整数/)
  state.lines[0].quantity = '0'
  state.draft.reason = '实盘为零'
  await state.save()
  assert.equal(writes[0].items[0].counted_on_hand, 0)
  assert.equal(writes[0].items[0].available_delta, undefined)
  state.draft.kind = 'damage'
  // Vue 的 number 输入会在真实页面自动转换数字，不能假设 v-model 一直是字符串。
  state.lines[0].productId = 7
  state.lines[0].quantity = -2
  await state.save()
  assert.equal(writes[1].items[0].available_delta, -2)
  assert.equal(writes[1].items[0].counted_on_hand, undefined)
})

test('inventory adjustment network failure preserves draft and its retry key', async (t) => {
  const writes = []
  const { state } = await mountBusinessFeature(t, 'inventory', 'AdjustmentPanel', {
    fetchAdjustments: async () => ({ items: [], total: 0 }),
    createAdjustment: async payload => { writes.push(payload); throw new Error('network failure') },
  })
  state.lines[0] = { productId: '7', quantity: '9' }
  state.draft.reason = '实盘核对'
  await state.save()
  await state.save()
  assert.equal(writes[0].idempotency_key, writes[1].idempotency_key)
  assert.equal(state.lines[0].quantity, '9')
})

test('extra parcel logistics sends its package number rather than querying the primary waybill', async (t) => {
  const calls = []
  const { state } = await mountShippingFeature(t, 'LogisticsPanel', {
    fetchTracking: async (...args) => { calls.push(['read', ...args]); return trackingFixture() },
    syncTracking: async (...args) => { calls.push(['sync', ...args]); return trackingFixture({ sync_result: 'updated' }) },
  }, { packageNo: 2 })
  await state.synchronize()
  assert.deepEqual(calls, [['read', 7, 2], ['sync', 7, 2]])
})


test('aftersale changes refresh order membership and both refund view counts', async (t) => {
  const row = { id: 7, status: 'completed', print_request_count: 0, refunded_amount: '0.00', aftersale_pending: true }
  let refunded = false
  const calls = []
  const browser = workbenchWindow('?status=aftersale_pending')
  const state = await mountView(t, 'OrdersView', workbenchServices({
    __window: browser,
    fetchOrders: async status => {
      calls.push(status)
      return { items: status === 'aftersale_pending' && refunded ? [] : [row], total: status === 'aftersale_pending' && refunded ? 0 : 1,
        status_counts: refunded ? { completed: 1, refunded: 1 } : { completed: 1, aftersale_pending: 1 } }
    },
    fetchOrder: async () => row,
  }))
  assert.equal(state.filter, 'aftersale_pending')
  await state.openOrder(state.orders[0])
  refunded = true
  row.refunded_amount = '5.00'
  row.aftersale_pending = false
  await state.refreshAfterSale()
  assert.equal(state.orders.length, 0)
  assert.equal(state.statusCounts.refunded, 1)
  assert.equal(state.selectedOrder, null)
  state.filter = 'refunded'
  await settle()
  await state.loadOrders()
  assert.equal(state.orders[0].status, 'completed')
  assert.equal(state.orders[0].refunded_amount, '5.00')
  assert(calls.includes('refunded'))
})


async function mountCatalogue(t, services) {
  const source = readFileSync(new URL('../src/features/products/composables/useProductsCatalog.ts', import.meta.url), 'utf8')
  const code = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } }).outputText
  const module = { exports: {} }
  runInNewContext(code, { module, exports: module.exports, require: id => id === 'vue' ? Vue : { useI18n: () => ({ t: key => key, translateValue: key => key }), fetchProductOptions: async () => ({}), ...services } })
  const app = renderer.createApp({ setup: () => module.exports.useProductsCatalog(), render: () => null })
  app.mount({})
  let mounted = true
  const unmount = () => { if (mounted) { app.unmount(); mounted = false } }
  t.after(unmount)
  await settle()
  return { state: app._instance.setupState, unmount }
}

async function mountFavorites(t, services) {
  const source = readFileSync(new URL('../src/features/products/composables/useProductFavorites.ts', import.meta.url), 'utf8')
  const code = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } }).outputText
  const module = { exports: {} }
  runInNewContext(code, { Error, module, exports: module.exports, require: id => id === 'vue' ? Vue : { useI18n: () => ({ t: key => key }), ...services } })
  const app = renderer.createApp({ setup: () => module.exports.useProductFavorites(), render: () => null })
  app.mount({})
  t.after(() => app.unmount())
  await settle()
  return app._instance.setupState
}

test('favorites restore server state and block repeated clicks until saving completes', async (t) => {
  const saved = deferred()
  let adds = 0
  let removes = 0
  const state = await mountFavorites(t, {
    fetchFavorites: async () => [{ id: 1 }],
    addFavorite: async () => { adds++; await saved.promise },
    removeFavorite: async () => { removes++ },
  })
  assert.equal(state.favoriteIds.includes(1), true)
  const saving = state.toggleFavorite({ id: 2 })
  await state.toggleFavorite({ id: 2 })
  assert.equal(adds, 1)
  assert.equal(state.favoriteIds.includes(2), false)
  saved.resolve()
  await saving
  assert.equal(state.favoriteIds.includes(2), true)
  await state.toggleFavorite({ id: 2 })
  assert.equal(removes, 1)
  assert.equal(state.favoriteIds.includes(2), false)
  assert.equal(state.favoriteIds.includes(1), true)
})

test('failed favorite mutations preserve saved state and allow retries', async (t) => {
  const state = await mountFavorites(t, {
    fetchFavorites: async () => [{ id: 1 }],
    addFavorite: async () => { throw new Error('save failed') },
    removeFavorite: async () => { throw new Error('remove failed') },
  })
  await state.toggleFavorite({ id: 2 })
  assert.equal(state.favoriteIds.includes(2), false)
  assert.equal(state.error, 'save failed')
  await state.toggleFavorite({ id: 1 })
  assert.equal(state.favoriteIds.includes(1), true)
  assert.equal(state.error, 'remove failed')
  assert.equal(state.pendingIds.length, 0)
})

test('favorites cannot overwrite an unknown saved state after loading fails', async (t) => {
  let fail = true
  let writes = 0
  const state = await mountFavorites(t, {
    fetchFavorites: async () => { if (fail) throw new Error('load failed'); return [] },
    addFavorite: async () => { writes++ },
  })
  assert.equal(state.ready, false)
  await state.toggleFavorite({ id: 1 })
  assert.equal(writes, 0)
  fail = false
  await state.loadFavorites()
  await state.toggleFavorite({ id: 1 })
  assert.equal(writes, 1)
})

test('catalogue ignores stale category responses and failures after a newer filter', async (t) => {
  const requests = []
  const { state } = await mountCatalogue(t, { fetchProducts: async filters => { const value = deferred(); requests.push({ value, status: filters.status }); return value.promise } })
  state.setStatus('Inactive')
  requests[1].value.resolve({ items: [{ id: 2, status: 'Inactive' }], total: 1 })
  await settle()
  requests[0].value.reject(new Error('outdated failure'))
  await settle()
  assert.equal(state.products[0].id, 2)
  assert.equal(state.error, '')
  assert.equal(state.loading, false)
})

test('catalogue returns to the last valid page after deleting the final product', async (t) => {
  const pages = []
  const { state } = await mountCatalogue(t, { fetchProducts: async filters => { pages.push(filters.page); return { items: filters.page > 1 ? [] : [{ id: 1 }], total: 1 } } })
  state.filters.page = 2
  await state.loadProducts()
  assert.deepEqual(pages, [1, 2, 1])
  assert.equal(state.filters.page, 1)
  assert.equal(state.products[0].id, 1)
})

test('an unmounted catalogue does not adopt a late response', async (t) => {
  const pending = deferred()
  const { state, unmount } = await mountCatalogue(t, { fetchProducts: async () => pending.promise })
  unmount()
  pending.resolve({ items: [{ id: 9 }], total: 1 })
  await settle()
  assert.equal(state.products.length, 0)
})

test('shipping handover is disabled while an order has pending aftersales', async (t) => {
  let dispatches = 0
  const order = shippingOrder(7, { shipment: shipmentRecord(), aftersale_pending: true })
  const state = await mountView(t, 'ShippingView', { fetchOrder: async () => order, dispatchShipment: async () => { dispatches++ } })
  await state.openOrder(7)
  assert.equal(state.canDispatch, false)
  await state.confirmDispatch()
  assert.equal(dispatches, 0)
})

test('bulk dispatch rechecks fresh aftersale holds even when selected rows are stale', async (t) => {
  const { state } = await mountShippingFeature(t, 'BulkShippingPanel', {
    fetchPackages: async () => packageFixture({ aftersale_pending: true }), request: async () => ({ configured: true }),
  }, { orders: [shippingOrder(7, { shipment: shipmentRecord() })] })
  await state.prepare()
  assert.equal(state.readyToDispatch, false)
  assert.match(state.error, /售后暂停/)
})

test('freight export refreshes changed filters and never exports after preview failure', async (t) => {
  let fail = false
  const downloads = []
  const queries = []
  const { state } = await mountShippingFeature(t, 'FreightPanel', {
    request: async path => { queries.push(path); if (fail) throw new Error('preview failed'); return { order_count: 1 } },
    downloadCsv: async path => downloads.push(path),
  })
  state.filters.carrier = '顺丰'
  await state.exportCsv()
  assert.equal(queries.at(-1).split('?')[1], downloads[0].split('?')[1])
  fail = true
  state.filters.carrier = '中通'
  await state.exportCsv()
  assert.equal(downloads.length, 1)
  assert.match(state.error, /preview failed/)
  assert.equal(state.report.order_count, 1)
})

const copilotPlan = () => ({ parameters: { keyword: '', coverage_days: 7, safety_percent: 15, minimum_stock: 5 }, items: [{ product_id: 1, sku_id: 1, suggested_quantity: 8, basis: 'a'.repeat(64) }] })
async function mountCopilot(t, services) {
  const component = compileComponent('../src/features/inventory/components/CopilotWorkbench.vue', {
    query: () => '', ...services,
  })
  component.render = () => null
  const app = renderer.createApp(component)
  app.mount({})
  t.after(() => app.unmount())
  await settle()
  return app._instance.setupState
}

test('copilot blocks writes after changing analysis parameters', async (t) => {
  let writes = 0
  const state = await mountCopilot(t, { request: async (path, method) => {
    if (method === 'POST') writes++
    return copilotPlan()
  } })
  state.selected = [1]
  state.parameters.coverage_days = 14
  await state.createDraft()
  assert.equal(writes, 0)
  assert.equal(state.unchanged, false)
})

test('copilot retries the same draft intent and prevents overlapping writes', async (t) => {
  const first = deferred()
  const writes = []
  const state = await mountCopilot(t, { request: async (path, method, body) => {
    if (method !== 'POST') return copilotPlan()
    writes.push(body)
    if (writes.length === 1) return first.promise
    return { id: 10, receipt_number: 'IN-EVAL' }
  } })
  state.selected = [1]
  const pending = state.createDraft()
  await state.createDraft()
  assert.equal(writes.length, 1)
  first.reject(new Error('response lost'))
  await pending
  await state.createDraft()
  assert.equal(writes.length, 2)
  assert.equal(writes[0].idempotency_key, writes[1].idempotency_key)
  assert.equal(state.plan, null)
  assert.match(state.notice, /库存尚未增加/)
})

test('copilot ignores a stale analysis response after switching modes', async (t) => {
  const old = deferred()
  const state = await mountCopilot(t, { request: async (path) => path.includes('shipping') ? { items: [] } : old.promise })
  state.changeMode('shipping')
  await settle()
  old.resolve(copilotPlan())
  await settle()
  assert.equal(state.plan, null)
  assert.equal(state.shipping.items.length, 0)
})

test('AI observability ignores older trace details when another trace is selected', async (t) => {
  const old = deferred()
  const state = await mountView(t, 'ObservabilityView', { request: async (path) => {
    if (path.endsWith('/traces/old')) return old.promise
    if (path.endsWith('/traces/new')) return { id:'new', feedback_note:'current' }
    return path.includes('/traces?') ? { items:[], total:0 } : { sampled_requests:0 }
  } })
  const pending = state.open('old')
  await state.open('new')
  old.resolve({ id:'old', feedback_note:'stale' })
  await pending
  assert.equal(state.selected.id,'new')
  assert.equal(state.note,'current')
})

test('AI feedback blocks overlapping submissions and allows retry after failure', async (t) => {
  const first = deferred()
  let writes = 0
  const component = compileComponent('../src/features/agent/AgentFeedback.vue', { request: async () => {
    writes++
    if (writes === 1) return first.promise
    return { rating:'helpful' }
  } })
  component.render = () => null
  const app = renderer.createApp(component, { traceId:'trace-test' })
  app.mount({})
  t.after(() => app.unmount())
  const state = app._instance.setupState
  const pending = state.send('helpful')
  await state.send('unhelpful')
  assert.equal(writes,1)
  first.reject(new Error('network failure'))
  await pending
  assert.equal(state.rating,'')
  await state.send('helpful')
  assert.equal(writes,2)
  assert.equal(state.rating,'helpful')
})

test('customer refresh keeps the newest list when an earlier request fails late', async (t) => {
  const first = deferred()
  let calls = 0
  const state = await mountView(t, 'CustomersView', { fetchCustomers: async () => ++calls === 1 ? first.promise : [{ id: 2 }], fetchCustomerAddresses: async () => [] })
  await state.loadCustomers()
  first.reject(new Error('old customer failure'))
  await settle()
  assert.equal(state.customers[0].id, 2)
  assert.equal(state.error, '')
})
