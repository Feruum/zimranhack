import { describe, expect, test } from 'bun:test'
import { existsSync, readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

describe('ThinkTogether identity', () => {
  test('every statically imported product image exists in public assets', () => {
    function check(directory) {
      for (const item of readdirSync(directory, { withFileTypes: true })) {
        const path = join(directory, item.name)
        if (item.isDirectory()) { check(path); continue }
        if (!/\.[jt]sx?$/.test(item.name)) continue
        const source = readFileSync(path, 'utf8')
        for (const match of source.matchAll(/from\s+['"](?:@public|public)\/([^'"]+\.(?:png|svg|webp|jpg|jpeg))['"]/g)) {
          expect(existsSync(new URL(`../public/${match[1]}`, import.meta.url))).toBe(true)
        }
      }
    }
    check(fileURLToPath(new URL('../components/', import.meta.url)))
    check(fileURLToPath(new URL('../app/', import.meta.url)))
  })
  test('every shipped translation uses the new product name', () => {
    for (const filename of readdirSync(new URL('../locales/', import.meta.url))) {
      if (!filename.endsWith('.json')) continue
      const translations = JSON.parse(readFileSync(new URL(`../locales/${filename}`, import.meta.url), 'utf8'))
      function check(value) {
        if (typeof value === 'string') {
          const displayText = value.replace(/(?:https?:\/\/|[\w.+-]+@)\S+|\b[\w-]+(?:\.[\w-]+)+(?::\d+)?/g, '')
          expect(displayText).not.toMatch(/\blearnhouse\b|\blighthpuse\b|\blighthouse\b/i)
        } else if (value && typeof value === 'object') Object.values(value).forEach(check)
      }
      check(translations)
    }
  })

})
