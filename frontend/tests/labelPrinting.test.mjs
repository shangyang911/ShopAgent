import assert from 'node:assert/strict'
import test from 'node:test'
import { loadApiModule } from './helpers/loadApiModule.mjs'

const { populateLabelPrintWindow } = loadApiModule('features/shipping/api/labelPrinting.ts')
function fakeWindow() {
  const nodes = []
  const element = tag => {
    const node = { tag, children: [], append(...children) { this.children.push(...children) }, replaceChildren(...children) { this.children = children } }
    nodes.push(node)
    return node
  }
  const popup = { opener: {}, prints: 0, print() { this.prints++ }, document: { createElement: element, head: element('head'), body: element('body') } }
  return { popup, nodes }
}
const labels = [1, 2].map(package_no => ({ order_number: '<订单文本>', package_no, tracking_number: `SFTEST00000${package_no}`, label_url: `https://api.kuaidi100.com/label/test-${package_no}` }))

test('batch print waits for every image and inserts order names as plain text', () => {
  const { popup, nodes } = fakeWindow()
  populateLabelPrintWindow(popup, labels)
  const button = nodes.find(n => n.tag === 'button')
  const images = nodes.filter(n => n.tag === 'img')
  assert.equal(popup.opener, null)
  assert.equal(button.disabled, true)
  assert.match(nodes.find(n => n.className === 'label-caption').textContent, /<订单文本>/)
  images[0].onload()
  assert.equal(button.disabled, true)
  images[1].onload()
  assert.equal(button.disabled, false)
  assert.equal(popup.prints, 0)
  button.onclick()
  assert.equal(popup.prints, 1)
})

test('failed or unready label images cannot enable batch print', () => {
  const { popup, nodes } = fakeWindow()
  populateLabelPrintWindow(popup, labels)
  const images = nodes.filter(n => n.tag === 'img')
  images[0].onload()
  images[1].onerror()
  assert.equal(nodes.find(n => n.tag === 'button').disabled, true)
  assert.match(nodes.find(n => n.tag === 'header').children[0].textContent, /尚未生成/)
  assert.equal(popup.prints, 0)
})
