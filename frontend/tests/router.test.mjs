import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { runInNewContext } from 'node:vm'
import { setImmediate } from 'node:timers/promises'
import test from 'node:test'
import ts from 'typescript'
import * as Vue from 'vue'

function fixture(t, start = '/orders?order_id=7', loggedIn = true) {
  const location = new URL(start, 'http://app.test')
  const documentEvents = new Map()
  const windowEvents = new Map()
  const pushed = []
  class Element {
    constructor(href, extra = {}) {
      Object.assign(
        this,
        { href: new URL(href, location).href, target: '', download: false },
        extra,
      )
    }
    closest() {
      return this
    }
    hasAttribute(name) {
      return name === 'download' && this.download
    }
    getAttribute(name) {
      return name === 'href' ? (this.rawHref ?? this.href) : null
    }
  }
  const modules = new Map()
  function load(name) {
    if (modules.has(name)) return modules.get(name)
    const source = readFileSync(new URL(`../src/router/${name}.ts`, import.meta.url), 'utf8')
    const code = ts.transpileModule(source, {
      compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
    }).outputText
    const module = { exports: {} }
    runInNewContext(code, {
      module,
      exports: module.exports,
      URL,
      Element,
      Event: class {
        constructor(type) {
          this.type = type
        }
      },
      require: (id) =>
        id === 'vue'
          ? Vue
          : id === './routes'
            ? load('routes')
            : { __esModule: true, default: { name: id.split('/').at(-1).replace('.vue', '') } },
      window: {
        location,
        history: {
          pushState: (_state, _title, url) => {
            pushed.push(url)
            location.href = url
          },
        },
        addEventListener: (key, handler) => windowEvents.set(key, handler),
        removeEventListener: (key) => windowEvents.delete(key),
        dispatchEvent: (event) => windowEvents.get(event.type)?.(event),
      },
      document: {
        addEventListener: (key, handler) => documentEvents.set(key, handler),
        removeEventListener: (key) => documentEvents.delete(key),
      },
    })
    modules.set(name, module.exports)
    return module.exports
  }
  const authenticated = Vue.ref(loggedIn)
  let state
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
  const app = renderer.createApp({
    setup() {
      state = load('index').useAppRouter(authenticated)
      return () => null
    },
  })
  app.mount({})
  t.after(() => app.unmount())
  function click(href, extra = {}) {
    const event = {
      target: new Element(href, extra.link),
      defaultPrevented: false,
      button: 0,
      preventDefault() {
        this.defaultPrevented = true
      },
      ...extra,
    }
    documentEvents.get('click')?.(event)
    return event
  }
  return {
    state,
    authenticated,
    location,
    documentEvents,
    windowEvents,
    pushed,
    click,
    app,
    routes: load('routes').routes,
  }
}

test('the route registry selects every management page and preserves query navigation', (t) => {
  const router = fixture(t)
  assert.equal(router.state.currentRoute.value.name, 'orders')
  assert.deepEqual(
    Array.from(router.routes, (route) => route.path),
    [
      '/',
      '/products',
      '/favorites',
      '/notifications',
      '/products/detail',
      '/inventory',
      '/orders',
      '/customers',
      '/shipping',
      '/agent',
      '/knowledge',
      '/ai-observability',
    ],
  )
  assert.equal(router.click('/shipping?order_id=7&status=in_delivery').defaultPrevented, true)
  assert.equal(router.state.currentRoute.value.component.name, 'ShippingView')
  assert.equal(router.location.search, '?order_id=7&status=in_delivery')
  router.location.href = 'http://app.test/products?page=2'
  router.windowEvents.get('popstate')()
  assert.equal(router.state.currentRoute.value.component.name, 'ProductsView')
})

test('router leaves external, new-tab, download and modified links to the browser', (t) => {
  const router = fixture(t)
  for (const [href, extra] of [
    ['https://other.test/orders', {}],
    ['/orders', { ctrlKey: true }],
    ['/orders', { button: 1 }],
    ['/orders', { link: { target: '_blank' } }],
    ['/orders', { link: { download: true } }],
    ['#anchor', { link: { rawHref: '#anchor' } }],
  ])
    assert.equal(router.click(href, extra).defaultPrevented, false)
  assert.equal(router.pushed.length, 0)
})

test('query-only navigation changes the view key while shipping keeps its cached instance', (t) => {
  const router = fixture(t, '/products/detail?id=3')
  const initial = router.state.routeKey.value
  router.click('/products/detail?id=4')
  assert.notEqual(router.state.routeKey.value, initial)
  router.click('/orders?order_id=1')
  const orderKey = router.state.routeKey.value
  router.click('/orders?order_id=2')
  assert.notEqual(router.state.routeKey.value, orderKey)
  router.click('/shipping?order_id=1')
  const shippingKey = router.state.routeKey.value
  router.location.href = 'http://app.test/shipping?order_id=2'
  router.windowEvents.get('popstate')()
  assert.equal(router.state.routeKey.value, shippingKey)
  let shippingUpdates = 0
  router.windowEvents.set('popstate', () => {
    shippingUpdates++
  })
  router.click('/shipping?order_id=3')
  assert.equal(shippingUpdates, 1)
})

test('authentication gates navigation and event listeners are removed on unmount', async (t) => {
  const router = fixture(t, '/shipping', false)
  assert.equal(router.click('/products').defaultPrevented, false)
  router.authenticated.value = true
  router.state.syncRoute()
  assert.equal(router.click('/products').defaultPrevented, true)
  router.app.unmount()
  await setImmediate()
  assert.equal(router.documentEvents.size, 0)
  assert.equal(router.windowEvents.size, 0)
})
