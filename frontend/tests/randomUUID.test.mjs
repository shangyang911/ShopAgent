import assert from 'node:assert/strict'
import { webcrypto } from 'node:crypto'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { runInNewContext } from 'node:vm'
import ts from 'typescript'

function loadInstaller() {
  const source = readFileSync(new URL('../src/shared/randomUUID.ts', import.meta.url), 'utf8')
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  })
  const exports = {}
  runInNewContext(outputText, { exports })
  return exports.installRandomUUID
}

test('HTTP browsers without randomUUID can generate distinct valid UUID v4 request IDs', () => {
  const installRandomUUID = loadInstaller()
  const browserCrypto = { getRandomValues: webcrypto.getRandomValues.bind(webcrypto) }
  installRandomUUID(browserCrypto)
  const ids = Array.from({ length: 1000 }, () => browserCrypto.randomUUID())
  assert.equal(new Set(ids).size, ids.length)
  for (const id of ids) {
    assert.match(id, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/)
  }
})

test('HTTPS browsers keep their native UUID generator', () => {
  const installRandomUUID = loadInstaller()
  const native = () => 'native-generator'
  const browserCrypto = { randomUUID: native }
  installRandomUUID(browserCrypto)
  assert.equal(browserCrypto.randomUUID, native)
})
