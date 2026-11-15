import { afterEach, describe, expect, mock, test } from 'bun:test'
import React, { act } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { readFileSync } from 'node:fs'
import { Window } from 'happy-dom'

const dom = new Window({ url: 'http://localhost/', width: 687, height: 700 })
for (const name of ['window', 'document', 'navigator', 'HTMLElement', 'HTMLInputElement', 'HTMLButtonElement', 'Element', 'Node', 'NodeFilter', 'DocumentFragment', 'MutationObserver', 'ResizeObserver', 'CustomEvent', 'Event', 'MouseEvent', 'KeyboardEvent', 'getComputedStyle', 'requestAnimationFrame', 'cancelAnimationFrame']) {
  globalThis[name] = name === 'window' ? dom : dom[name]
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true
const { createRoot } = await import('react-dom/client')
const requests = []
const itemId = '00000000-0000-0000-0000-000000000001'
mock.module('@services/thinktogether/api', () => ({
  useTTQuery: () => ({ data: undefined }),
  ttPost: async path => {
    requests.push(path)
    return { delivery_id: 'delivery-one', item: { id: itemId, kind: 'single_choice', title: 'Bound practice case', prompt: 'Choose a response.', choices: [{ id: 'a', text: 'First response' }] } }
  },
  ttApi: () => Promise.resolve(),
}))
mock.module('../components/ThinkTogether/Shell', () => ({ styles: { prose: 'prose' } }))
mock.module('react-i18next', () => ({ useTranslation: () => ({ t: key => key }) }))
mock.module('next/link', () => ({ default: ({ children, href, ...props }) => React.createElement('a', { href, ...props }, children) }))
mock.module('next/image', () => ({ default: ({ src, alt, width, height, className }) => React.createElement('img', { src, alt, width, height, className }) }))
const { default: CourseMarkdown } = await import('../components/ThinkTogether/CourseMarkdown')
const { default: BoardToolbar } = await import('../components/Dashboard/Boards/BoardToolbar')
const css = readFileSync(new URL('../styles/globals.css', import.meta.url), 'utf8')
let root

afterEach(async () => {
  if (root) await act(async () => root.unmount())
  root = undefined
  document.body.innerHTML = ''
  document.head.innerHTML = ''
  requests.length = 0
})

const chapter = text => `:::exercise c1_q1\n${text}\n:::`
const lesson = text => React.createElement(CourseMarkdown, { lessonId: 'lesson-one' }, chapter(text))
async function mount(element) {
  const host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
  await act(async () => root.render(element))
  return host
}
function toolbarProps(overrides = {}) {
  return {
    toolMode: 'select', onToolModeChange: () => undefined,
    editor: { can: () => ({ undo: () => false, redo: () => false }) },
    drawColor: '#000000', drawWidth: 3,
    onDrawColorChange: () => undefined, onDrawWidthChange: () => undefined,
    ...overrides,
  }
}
function styledToolbar(width) {
  dom.happyDOM.setWindowSize({ width, height: 700 })
  const style = document.createElement('style')
  // The flex/bottom utilities are supplied by Tailwind in the app. All board
  // rules and media queries below are the actual global stylesheet.
  style.textContent = `.flex { display: flex; } .bottom-5 { bottom: 20px; }\n${css}`
  document.head.append(style)
  document.body.innerHTML = renderToStaticMarkup(React.createElement(BoardToolbar, toolbarProps()))
  return document.querySelector('.board-enter-toolbar')
}

describe('compiled lesson exercise rendering', () => {
  test('a marker-only exercise hides its binding and keeps the real start control', () => {
    const html = renderToStaticMarkup(lesson(`{{item:${itemId}}}`))
    expect(html).not.toContain('{{item:')
    expect(html).toContain('Start exercise')
    expect(html).not.toContain('Exercise preparation in progress.')
  })

  test('prose alongside a bound exercise renders as Markdown without its marker', () => {
    const html = renderToStaticMarkup(lesson(`Try **this case**.\n{{item:${itemId}}}`))
    expect(html).toContain('Try <strong>this case</strong>.')
    expect(html).not.toContain('{{item:')
    expect(html).toContain('Start exercise')
  })

  test('missing and multiple bindings cannot start an arbitrary exercise', () => {
    for (const text of ['{{csv:c1_q1}}', '{{item:invalid}}', `{{item:${itemId}}} {{item:00000000-0000-0000-0000-000000000002}}`]) {
      const html = renderToStaticMarkup(lesson(text))
      expect(html).toContain('Exercise preparation in progress.')
      expect(html).not.toContain('Start exercise')
    }
  })

  test('the marker-only exercise opens its bound lesson delivery', async () => {
    const host = await mount(lesson(`{{item:${itemId}}}`))
    await act(async () => host.querySelector('button').click())
    expect(requests).toEqual([`lessons/lesson-one/exercises/${itemId}`])
    expect(host.textContent).toContain('Bound practice case')
    expect(host.querySelector('input[type="radio"]')).not.toBeNull()
  })

  test.each(['```', '~~~'])('keeps a fenced %s marker literal while opening the actual exercise', async fence => {
    const token = `{{item:${itemId}}}`
    const host = await mount(lesson(`${fence}text\n${token}\n${fence}\nTry the bound case.\n${token}`))
    expect(host.querySelector('pre code').textContent.trim()).toBe(token)
    expect(host.textContent.match(/\{\{item:/g)).toHaveLength(1)
    expect(host.textContent).toContain('Try the bound case.')
    const start = [...host.querySelectorAll('button')].find(button => button.textContent === 'Start exercise')
    expect(start).toBeDefined()
    await act(async () => start.click())
    expect(requests).toEqual([`lessons/lesson-one/exercises/${itemId}`])
    expect(host.textContent).toContain('Bound practice case')
  })
})

describe('embedded board toolbar', () => {
  test.each([687, 390])('keeps controls reachable with touch targets at %spx', width => {
    const toolbar = styledToolbar(width)
    const layout = getComputedStyle(toolbar)
    expect(layout.display).toBe('flex')
    expect(layout.overflowX).toBe('auto')
    expect(layout.maxWidth).toBe('calc(100% - 24px)')
    // The social controls remain at bottom:20px; the toolbar needs its own row.
    expect(parseFloat(layout.bottom.replace(/^calc\(/, ''))).toBeGreaterThanOrEqual(72)
    for (const control of toolbar.querySelectorAll('.editor-tool-btn')) {
      expect(control.tagName).toBe('BUTTON')
      expect(control.getAttribute('aria-label')).toBeTruthy()
      expect(parseInt(getComputedStyle(control).minWidth)).toBeGreaterThanOrEqual(44)
      expect(parseInt(getComputedStyle(control).height)).toBeGreaterThanOrEqual(44)
    }
  })

  test.each([858, 1250])('preserves the normal desktop toolbar at %spx', width => {
    const toolbar = styledToolbar(width)
    expect(getComputedStyle(toolbar).display).toBe('flex')
    expect(toolbar.querySelectorAll('.editor-tool-btn')).toHaveLength(16)
    expect(getComputedStyle(toolbar).bottom).toBe('20px')
    expect(parseInt(getComputedStyle(toolbar.querySelector('.editor-tool-btn')).height)).toBeLessThan(44)
  })

  test('named native buttons select notes and drawing and expose disabled history', async () => {
    const modes = []
    const host = await mount(React.createElement(BoardToolbar, toolbarProps({ onToolModeChange: mode => modes.push(mode) })))
    const select = host.querySelector('button[aria-label="boards.toolbar.select"]')
    expect(select).not.toBeNull()
    expect(select.getAttribute('aria-pressed')).toBe('true')
    const note = host.querySelector('button[aria-label="boards.toolbar.note"]')
    expect(note).not.toBeNull()
    await act(async () => note.click())
    const draw = host.querySelector('button[aria-label="boards.toolbar.draw"]')
    expect(draw).not.toBeNull()
    await act(async () => draw.click())
    expect(modes).toEqual(['note', 'draw'])
    expect(document.querySelector('button[aria-label*="#000000"]')).not.toBeNull()
    expect(host.querySelector('button[aria-label="boards.toolbar.undo"]').disabled).toBe(true)
    expect(host.querySelector('button[aria-label="boards.toolbar.redo"]').disabled).toBe(true)
  })
})
