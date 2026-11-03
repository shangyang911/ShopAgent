import { readFileSync, readdirSync, existsSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import ts from 'typescript'
import { parse } from 'vue/compiler-sfc'

const sourceRoot = fileURLToPath(new URL('../src/', import.meta.url))
const walk = (directory) =>
  readdirSync(directory, { withFileTypes: true }).flatMap((entry) =>
    entry.isDirectory()
      ? walk(path.join(directory, entry.name))
      : [path.join(directory, entry.name)],
  )
const files = walk(sourceRoot)
const graph = new Map()
const problems = []
for (const file of files) {
  const raw = readFileSync(file, 'utf8')
  const source = file.endsWith('.vue')
    ? (() => {
        const { descriptor } = parse(raw)
        return [descriptor.script?.content, descriptor.scriptSetup?.content]
          .filter(Boolean)
          .join('\n')
      })()
    : raw
  const tree = ts.createSourceFile(file, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS)
  const dependencies = []
  for (const node of tree.statements) {
    if ((!ts.isImportDeclaration(node) && !ts.isExportDeclaration(node)) || !node.moduleSpecifier)
      continue
    const specifier = node.moduleSpecifier.text
    if (!specifier.startsWith('.')) continue
    const base = path.resolve(path.dirname(file), specifier)
    const resolved = [base, base + '.ts', base + '.vue', path.join(base, 'index.ts')].find(
      (candidate) => files.includes(candidate) && existsSync(candidate),
    )
    if (!resolved) {
      problems.push(`${path.relative(sourceRoot, file)}: missing import ${specifier}`)
      continue
    }
    dependencies.push(resolved)
    if (
      file.startsWith(path.join(sourceRoot, 'shared') + path.sep) &&
      resolved.startsWith(path.join(sourceRoot, 'features') + path.sep)
    )
      problems.push(`${path.relative(sourceRoot, file)}: shared must not import a feature`)
  }
  graph.set(file, dependencies)
}
const reached = new Set()
function visit(file) {
  if (reached.has(file)) return
  reached.add(file)
  for (const dependency of graph.get(file) || []) visit(dependency)
}
visit(path.join(sourceRoot, 'main.ts'))
for (const file of files) {
  if (!file.endsWith('.d.ts') && !reached.has(file))
    problems.push(`${path.relative(sourceRoot, file)}: unreachable from main.ts`)
}
if (problems.length) {
  console.error(problems.join('\n'))
  process.exitCode = 1
} else {
  console.log(
    `Architecture check passed: ${reached.size} reachable files, shared layer has no feature dependencies.`,
  )
}
