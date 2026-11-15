import { expect, test } from 'bun:test'
import { parseCourseBlocks, exerciseBinding } from '../services/thinktogether/course-blocks'

test('renders ordinary Markdown and distinct lesson block types', () => {
  const blocks = parseCourseBlocks('# Chapter\n:::explain\nConcept\n:::\n:::example\nExample\n:::\n:::exercise c1_q1\n{{item:00000000-0000-0000-0000-000000000001}}\n:::')
  expect(blocks.map(block => block.type)).toEqual(['markdown', 'explain', 'example', 'exercise'])
  expect(exerciseBinding(blocks[3].text)?.itemId).toBe('00000000-0000-0000-0000-000000000001')
  expect(parseCourseBlocks('Plain **Markdown**')).toEqual([{ type: 'markdown', text: 'Plain **Markdown**' }])
})
test('treats tags inside code fences as code and rejects unknown or nested tags', () => {
  expect(parseCourseBlocks('```md\n:::unknown\n```\n:::reflection\nConsider alternatives\n:::').map(b => b.type)).toEqual(['markdown', 'reflection'])
  for (const source of [':::unknown\ntext\n:::', ':::example\n:::explain\ntext\n:::\n:::', ':::example\nunclosed']) expect(() => parseCourseBlocks(source)).toThrow()
})
test('does not bind incomplete or duplicate exercise tokens', () => {
  expect(exerciseBinding('{{csv:c1_q1}}')).toBeNull()
  expect(exerciseBinding('{{item:00000000-0000-0000-0000-000000000001}} {{item:00000000-0000-0000-0000-000000000002}}')).toBeNull()
  expect(() => parseCourseBlocks(':::exercise c1_q1\nx\n:::\n:::exercise c1_q1\nx\n:::')).toThrow()
})

test.each(['```', '~~~'])('binds only an unfenced marker and preserves %s examples', fence => {
  const itemId = '00000000-0000-0000-0000-000000000001'
  const token = `{{item:${itemId}}}`
  // Use the same marker in code and prose to catch replacement of the wrong occurrence.
  const literal = `${fence}text\n${token}\n${token}\n${fence}`
  expect(exerciseBinding(`${literal}\nTry this case. ${token}`)).toEqual({ itemId, text: `${literal}\nTry this case.` })
  expect(exerciseBinding(literal)).toBeNull()
  expect(exerciseBinding(`${literal}\n${token} ${token}`)).toBeNull()
})

test('keeps CRLF fenced text intact and does not close a fence with a different delimiter', () => {
  const itemId = '00000000-0000-0000-0000-000000000001'
  const token = `{{item:${itemId}}}`
  const literal = `~~~text\r\n${token}\r\n~~~`
  expect(exerciseBinding(`${literal}\r\n${token}`)).toEqual({ itemId, text: literal })
  expect(exerciseBinding(`\`\`\`text\n~~~\n${token}`)).toBeNull()
})

test('accepts the same directive whitespace as the API parser', () => {
  const token = '{{item:00000000-0000-0000-0000-000000000001}}'
  for (const opener of ['::: exercise c1_q1', ':::exercise\tc1_q1']) {
    expect(parseCourseBlocks(`${opener}\n${token}\n:::`)).toEqual([{ type: 'exercise', id: 'c1_q1', text: token }])
  }
})
