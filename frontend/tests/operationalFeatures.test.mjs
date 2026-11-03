import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { runInNewContext } from 'node:vm'
import { setImmediate } from 'node:timers/promises'
import test from 'node:test'
import ts from 'typescript'
import * as Vue from 'vue'

const settle = async () => {
  await Vue.nextTick()
  await setImmediate()
}
const deferred = () => {
  let resolve, reject
  const promise = new Promise((yes, no) => {
    resolve = yes
    reject = no
  })
  return { promise, resolve, reject }
}
const renderer = Vue.createRenderer({
  createComment: () => ({}),
  insert() {},
  remove() {},
  parentNode: () => null,
  nextSibling: () => null,
  setText() {},
  setElementText() {},
  patchProp() {},
})

async function mount(t, feature, name, services, args = []) {
  const source = readFileSync(
    new URL(`../src/features/${feature}/composables/${name}.ts`, import.meta.url),
    'utf8',
  )
  const code = ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  }).outputText
  const module = { exports: {} }
  let keyCounter = 0
  runInNewContext(code, {
    module,
    exports: module.exports,
    Error,
    setInterval: services.__setInterval ?? (() => 1),
    clearInterval() {},
    crypto: { randomUUID: () => `key-${++keyCounter}` },
    window: { confirm: () => true, addEventListener() {}, removeEventListener() {} },
    require: (id) => (id === 'vue' ? Vue : services),
  })
  let state
  const app = renderer.createApp({
    setup() {
      state = module.exports[name](...args)
      return () => null
    },
  })
  if (services.__navigation) app.provide(services.notificationNavigationKey, services.__navigation)
  app.mount({})
  let mounted = true
  const unmount = () => {
    if (mounted) {
      app.unmount()
      mounted = false
    }
  }
  t.after(unmount)
  await settle()
  return { state, unmount }
}

test('notification filters discard an old list response and read never changes business state locally', async (t) => {
  const requests = [],
    reads = []
  const { state } = await mount(t, 'notifications', 'useNotifications', {
    notificationNavigationKey: Symbol(),
    fetchNotifications: (...args) => {
      const item = deferred()
      requests.push({ ...item, args })
      return item.promise
    },
    markNotificationsRead: async (keys) => {
      reads.push(keys)
    },
  })
  state.category.value = 'stock'
  state.applyFilters()
  requests[1].resolve({ items: [{ key: 'stock:2', read_at: null }], total: 1 })
  await settle()
  requests[0].reject(new Error('outdated'))
  await settle()
  assert.equal(state.items.value[0].key, 'stock:2')
  assert.equal(state.error.value, '')
  const read = state.markRead(['stock:2'])
  await settle()
  requests[2].resolve({ items: [{ key: 'stock:2', read_at: '2026-10-31T00:00:00Z' }], total: 1 })
  await read
  assert.equal(reads.length, 1)
  assert.equal(state.items.value.length, 1)
  assert(state.items.value[0].read_at)
})

test('notification list returns to a valid page when issues are resolved', async (t) => {
  const pages = []
  const { state } = await mount(t, 'notifications', 'useNotifications', {
    notificationNavigationKey: Symbol(),
    fetchNotifications: async (_category, _state, page) => {
      pages.push(page)
      return { items: [], total: 0 }
    },
  })
  state.changePage(2)
  await settle()
  assert.equal(state.page.value, 1)
  assert.deepEqual(pages, [1, 2, 1])
})

test('marking one page read keeps other-page unread counts and mark-all synchronizes the sidebar', async (t) => {
  const badge = Vue.ref(25)
  let unread = 25
  const navigation = {
    setUnreadCount: (count) => {
      badge.value = count
    },
    refresh: async () => {},
  }
  const { state } = await mount(t, 'notifications', 'useNotifications', {
    notificationNavigationKey: Symbol(),
    __navigation: navigation,
    fetchNotifications: async () => ({
      items: [{ key: 'stock:1', read_at: unread < 25 ? 'read' : null }],
      total: 25,
      active_count: 25,
      unread_count: unread,
    }),
    markNotificationsRead: async () => {
      unread = 5
    },
    markAllNotificationsRead: async () => {
      unread = 0
    },
  })
  assert.equal(state.activeCount.value, 25)
  await state.markRead(['stock:1'])
  assert.equal(state.unreadCount.value, 5)
  assert.equal(badge.value, 5)
  await state.markAllRead()
  assert.equal(state.unreadCount.value, 0)
  assert.equal(badge.value, 0)
  assert.equal(state.activeCount.value, 25)
  state.category.value = 'shipment'
  state.showUnread()
  assert.equal(state.category.value, 'all')
  assert.equal(state.state.value, 'unread')
})

test('a late badge poll cannot restore the red badge after the list reports zero unread', async (t) => {
  const requests = []
  const { state } = await mount(
    t,
    'notifications',
    'useNotificationBadge',
    {
      fetchNotifications: () => {
        const item = deferred()
        requests.push(item)
        return item.promise
      },
    },
    [Vue.ref(true)],
  )
  state.setUnreadCount(0)
  requests[0].resolve({ unread_count: 17 })
  await settle()
  assert.equal(state.unreadCount.value, 0)
  const current = state.refresh()
  requests[1].resolve({ unread_count: 1 })
  await current
  assert.equal(state.unreadCount.value, 1)
})

test('failed mark-all preserves unread counts and a late write does not reload an unmounted page', async (t) => {
  let reads = 0
  let fail = true
  const pending = deferred()
  const { state, unmount } = await mount(t, 'notifications', 'useNotifications', {
    notificationNavigationKey: Symbol(),
    fetchNotifications: async () => {
      reads++
      return { items: [], total: 2, active_count: 2, unread_count: 2 }
    },
    markAllNotificationsRead: async () => {
      if (fail) throw new Error('写入失败')
      await pending.promise
    },
  })
  await state.markAllRead()
  assert.equal(state.unreadCount.value, 2)
  assert.equal(state.error.value, '写入失败')
  fail = false
  const writing = state.markAllRead()
  unmount()
  pending.resolve()
  await writing
  assert.equal(reads, 1)
})

test('batch preview is required and transport retries reuse their submission key', async (t) => {
  const products = Vue.ref([{ id: 1 }, { id: 2 }]),
    selected = Vue.ref([1, 2])
  const calls = []
  const { state } = await mount(
    t,
    'products',
    'useProductBatch',
    {
      previewProductBatch: async () => ({ snapshot: 'version', items: [{ id: 1 }, { id: 2 }] }),
      applyProductBatch: async (...args) => {
        calls.push(args)
        if (calls.length === 1) throw new Error('network')
        return { count: 2 }
      },
    },
    [products, selected, () => {}],
  )
  await state.apply()
  assert.equal(calls.length, 0)
  await state.prepare()
  await state.apply()
  assert(state.preview.value)
  assert.equal(state.error.value, 'network')
  await state.apply()
  assert.equal(calls[0][3], calls[1][3])
  assert.equal(selected.value.length, 0)
  assert.match(state.success.value, /2/)
})

test('batch selection changes invalidate pending preview and a changed page clears selection', async (t) => {
  const pending = deferred()
  const products = Vue.ref([{ id: 1 }, { id: 2 }]),
    selected = Vue.ref([1])
  const { state } = await mount(
    t,
    'products',
    'useProductBatch',
    { previewProductBatch: () => pending.promise },
    [products, selected, () => {}],
  )
  const request = state.prepare()
  selected.value = [2]
  pending.resolve({ snapshot: 'stale', items: [{ id: 1 }] })
  await request
  assert.equal(state.preview.value, null)
  products.value = [{ id: 3 }]
  await settle()
  assert.equal(selected.value.length, 0)
})

const customer = (id) => ({
  customer: { id },
  profile: { tags: ['老客户'], note: '原备注', revision: 1 },
  orders: { total: 11, items: [] },
})

test('a late favorites refresh cannot overwrite the latest saved state or revive an unmounted page', async (t) => {
  const requests = []
  const { state, unmount } = await mount(t, 'products', 'useProductFavorites', {
    useI18n: () => ({ t: (key) => key }),
    fetchFavorites: () => {
      const request = deferred()
      requests.push(request)
      return request.promise
    },
  })
  const latest = state.loadFavorites()
  requests[1].resolve([{ id: 2 }])
  await latest
  requests[0].resolve([{ id: 1 }])
  await settle()
  assert.equal(state.favorites.value[0].id, 2)
  const pending = state.loadFavorites()
  unmount()
  requests[2].resolve([{ id: 3 }])
  await pending
  assert.equal(state.favorites.value[0].id, 2)
})

test('shrinking customer order totals returns to the last valid page without dropping a profile draft', async (t) => {
  const pages = []
  const { state } = await mount(
    t,
    'customers',
    'useCustomerDetail',
    {
      fetchCustomerDetail: async (id, page) => {
        pages.push(page)
        return { ...customer(id), orders: { total: 1, items: page === 1 ? [{ id: 1 }] : [] } }
      },
    },
    [Vue.ref(1)],
  )
  state.noteDraft.value = '未保存的跟进'
  state.changePage(2)
  await settle()
  assert.equal(state.page.value, 1)
  assert.equal(state.data.value.orders.items.length, 1)
  assert.equal(state.noteDraft.value, '未保存的跟进')
  assert.deepEqual(pages, [1, 2, 1])
})
test('customer details ignore previous customer responses and preserve drafts when paging', async (t) => {
  const pending = deferred(),
    selected = Vue.ref(1)
  const { state } = await mount(
    t,
    'customers',
    'useCustomerDetail',
    {
      fetchCustomerDetail: async (id) => (id === 1 ? pending.promise : customer(id)),
    },
    [selected],
  )
  selected.value = 2
  await settle()
  pending.resolve(customer(1))
  await settle()
  assert.equal(state.data.value.customer.id, 2)
  state.noteDraft.value = '未保存备注'
  state.changePage(2)
  await settle()
  assert.equal(state.noteDraft.value, '未保存备注')
})

test('failed customer profile save preserves the draft and an old save cannot update another customer', async (t) => {
  let fail = true
  const pending = deferred(),
    selected = Vue.ref(1)
  const { state } = await mount(
    t,
    'customers',
    'useCustomerDetail',
    {
      fetchCustomerDetail: async (id) => customer(id),
      saveCustomerProfile: async () => {
        if (fail) throw new Error('版本冲突')
        return pending.promise
      },
    },
    [selected],
  )
  state.noteDraft.value = '新的备注'
  await state.saveProfile()
  assert.equal(state.noteDraft.value, '新的备注')
  assert.equal(state.profileError.value, '版本冲突')
  fail = false
  const save = state.saveProfile()
  selected.value = 2
  await settle()
  pending.resolve({ tags: [], note: '不应覆盖', revision: 2 })
  await save
  assert.equal(state.data.value.customer.id, 2)
  assert.equal(state.noteDraft.value, '原备注')
})
