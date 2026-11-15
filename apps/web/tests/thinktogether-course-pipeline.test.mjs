import { beforeEach, expect, mock, test } from 'bun:test'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'

let current
const mutations = []
mock.module('@services/thinktogether/api', () => ({
  useTTQuery: () => ({ data: current ? [current] : [] }),
  ttPost: (...args) => { mutations.push(args); return Promise.resolve(current) },
  ttApi: () => Promise.resolve(current),
}))
mock.module('@tanstack/react-query', () => ({
  useQuery: () => ({ data: current }),
  useQueryClient: () => ({ invalidateQueries: () => Promise.resolve(), setQueryData: () => undefined }),
}))
mock.module('../components/ThinkTogether/Shared', () => ({ ErrorNotice: () => null }))
mock.module('../components/ThinkTogether/Shell', () => ({ styles: { panel: 'panel', muted: 'muted', prose: 'prose' } }))
const { default: CourseGenerator } = await import('../components/ThinkTogether/CourseGenerator')
const render = () => renderToStaticMarkup(React.createElement(CourseGenerator))
const diagram = '/api/v1/tt/content/course-pipeline/one/artifacts/diagrams/ch01_d01.svg'
function run() {
  return { id: 'one', title: 'Учебный курс', status: 'done', stage: 'complete', created_at: '2026-11-15T00:00:00Z',
    coverage: { message: 'Документ обработан частично.', selected_chunks: 12, total_chunks: 30, partial: true },
    diagrams: [{ id: 'ch01_d01', label: 'Последовательность', url: diagram }],
    markdown: `<!-- TT:COURSE version="1" chapters="3" -->\n# Учебный курс\n\n![Последовательность](${diagram})\n\n![Внешняя картинка](https://external.invalid/tracker.svg)\n\n<script>alert(1)</script>`,
    download_url: '/api/v1/tt/content/course-pipeline/one/artifacts/course.final.md' }
}
beforeEach(() => { current = undefined; mutations.length = 0 })

test('renders the document and topic form without starting generation', () => {
  const html = render()
  expect(html).toContain('Мини-курс из документа')
  expect(html).toContain('accept=".pdf,.txt,.md,.docx,.pptx"')
  expect(html).toContain('for="course-topic"')
  expect(html).toContain('До 10 МБ')
  expect(mutations).toEqual([])
})

test('renders actual Markdown and private SVG and filters arbitrary images and HTML', () => {
  current = run()
  const html = render()
  expect(html).toContain('<h1>Учебный курс</h1>')
  expect(html).toContain(`src="${diagram}"`)
  expect(html).toContain('Документ обработан частично.')
  expect(html).toContain('12 из 30')
  expect(html).toContain('Скачать Markdown')
  expect(html).not.toContain('external.invalid')
  expect(html).not.toContain('<script>')
  expect(html).not.toContain('TT:COURSE')
})

test('shows the persisted second-stage progress', () => {
  current = { ...run(), status: 'retry', stage: 'diagrams', markdown: null }
  expect(render()).toContain('Готовим схемы и проверяем курс…')
})

test('shows a controlled failure with a recoverable draft', () => {
  current = { ...run(), status: 'error', markdown: null, download_url: undefined,
    error: 'Ответ модели не соответствует протоколу.', draft_url: '/api/v1/tt/content/course-pipeline/one/artifacts/course.draft.md' }
  const html = render()
  expect(html).toContain('role="alert"')
  expect(html).toContain('Скачать черновик')
  expect(html).not.toContain('Скачать Markdown')
})
