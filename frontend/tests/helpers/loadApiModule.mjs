import { readFileSync } from 'node:fs'
import { runInNewContext } from 'node:vm'
import ts from 'typescript'
import axios from 'axios'

// 用真实共享模块验证 API 调用链，只替换网络和浏览器环境，避免重构后测试仍只验证旧副本。
export function loadApiModule(path, { fetch, apiBase = 'http://api.test/api', imports = {} } = {}) {
  const modules = new Map()
  function load(url) {
    if (modules.has(url.href)) return modules.get(url.href).exports
    const source = readFileSync(url, 'utf8').replaceAll(
      'import.meta.env.VITE_API_BASE_URL',
      'testApiBase',
    )
    const code = ts.transpileModule(source, {
      compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
    }).outputText
    const module = { exports: {} }
    modules.set(url.href, module)
    runInNewContext(code, {
      module,
      exports: module.exports,
      require: (name) => {
        if (Object.hasOwn(imports, name)) return imports[name]
        if (name === 'axios')
          return {
            ...axios,
            create: (defaults) =>
              axios.create({
                ...defaults,
                adapter: async (config) => {
                  const headers = config.headers.toJSON()
                  if (config.data instanceof FormData) delete headers['Content-Type']
                  const answer = await fetch(axios.getUri(config), {
                    method: config.method.toUpperCase(),
                    credentials: config.withCredentials ? 'include' : 'omit',
                    headers,
                    body: config.data,
                  })
                  let data
                  try {
                    data =
                      config.responseType === 'text'
                        ? await answer.text()
                        : config.responseType === 'blob'
                          ? await answer.blob()
                          : await answer.json()
                  } catch {
                    data = null
                  }
                  const response = {
                    data,
                    status: answer.status ?? (answer.ok ? 200 : 500),
                    statusText: '',
                    headers: {},
                    config,
                  }
                  if (!answer.ok)
                    throw new axios.AxiosError(
                      'Request failed',
                      'ERR_BAD_RESPONSE',
                      config,
                      undefined,
                      response,
                    )
                  return response
                },
              }),
          }
        if (!name.startsWith('.')) throw new Error(`Unexpected dependency: ${name}`)
        return load(new URL(`${name}.ts`, url))
      },
      testApiBase: apiBase,
      window: { location: { hostname: 'app.test' } },
      URLSearchParams,
      FormData,
      Blob,
      Error,
      fetch,
    })
    return module.exports
  }
  return load(new URL(`../../src/${path}`, import.meta.url))
}
