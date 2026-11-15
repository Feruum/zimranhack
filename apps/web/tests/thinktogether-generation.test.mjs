import { beforeEach, expect, mock, test } from 'bun:test'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'

let current
const mutations = []
mock.module('@services/thinktogether/api', () => ({
  useTTQuery: path => ({ data: path === 'content/materials' ? [] : current ? [current] : [] }),
  ttPost: (...args) => { mutations.push(args); return Promise.resolve(current) },
  ttApi: () => Promise.resolve(current),
}))
mock.module('@tanstack/react-query', () => ({
  useQuery: () => ({ data: current, refetch: () => Promise.resolve() }),
  useQueryClient: () => ({ invalidateQueries: () => Promise.resolve(), setQueryData: () => undefined }),
}))
mock.module('../components/ThinkTogether/Shared', () => ({ ErrorNotice: () => null, Loading: ({ children }) => React.createElement('p', { role: 'status' }, children) }))
mock.module('../components/ThinkTogether/Shell', () => ({ styles: { panel: 'panel', muted: 'muted' } }))
mock.module('../components/ThinkTogether/CourseMarkdown', () => ({ default: ({ children }) => React.createElement('div', null, children) }))
const { default: Builder, generationRunning } = await import('../components/ThinkTogether/expert/AICourseBuilder')
function render() { return renderToStaticMarkup(React.createElement(Builder)) }
function run(stage = 'modules', status = 'retry') {
  return { id: 'run-one', title: 'Original PMP course', status, stage, program_id: 'program-one', completed_steps: 3,
    total_steps: 64, revision: 3, counts: { lessons: 2, practice_items: 10, mock_items: 0 },
    modules: [{ skill_id: 'skill-one', title: 'Stakeholder decisions', status: 'draft', items: 5 }],
    events: [{ id: 3, stage, message: 'Lesson saved as a draft.' }] }
}
beforeEach(() => { current = undefined; mutations.length = 0 })

test('offers real course generation and does not generate on render', () => {
  const html = render()
  expect(html).toContain('Create course')
  expect(html).toContain('Audience')
  expect(html).toContain('three-chapter demo')
  expect(html).toContain('Add materials or paste text (optional)')
  expect(html).not.toContain('Publish approved course')
  expect(mutations).toEqual([])
})
test('shows saved progress incrementally and allows stopping between steps', () => {
  current = run()
  const html = render()
  expect(html).toContain('value="3"')
  expect(html).toContain('2 lessons · 10 practice cases')
  expect(html).toContain('Lesson saved as a draft.')
  expect(html).toContain('Stop after current step')
  expect(html).not.toContain('Approve reviewed package')
})
test('review requires an explicit expert confirmation before approval', () => {
  current = run('ready_for_review', 'done')
  const html = render()
  expect(html).toContain('Approve reviewed package')
  expect(html).toContain('disabled=""')
  expect(html).toContain('I have checked the content, answer keys')
  expect(html).not.toContain('Publish approved course')
  expect(mutations).toEqual([])
})
test('publication is separate and approved content can be returned to draft', () => {
  current = run('approved', 'done')
  const html = render()
  expect(html).toContain('Publish approved course')
  expect(html).toContain('Return to draft')
  expect(html).toContain('I confirm that the approved package should be published.')
  expect(mutations).toEqual([])
})
test('only non-terminal durable jobs keep live polling active', () => {
  expect(generationRunning(run())).toBe(true)
  expect(generationRunning(run('modules', 'running'))).toBe(true)
  expect(generationRunning(run('modules', 'dead'))).toBe(false)
  expect(generationRunning(run('cancelled', 'done'))).toBe(false)
})
