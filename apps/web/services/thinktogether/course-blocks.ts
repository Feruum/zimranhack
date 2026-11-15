export type CourseBlock = { type: 'markdown' | 'explain' | 'example' | 'reflection' | 'exercise'; text: string; id?: string }
const tags = new Set(['explain', 'example', 'reflection', 'exercise'])

export function parseCourseBlocks(markdown: string): CourseBlock[] {
  if (!/^\s*:::/m.test(markdown)) return [{ type: 'markdown', text: markdown }]
  const blocks: CourseBlock[] = [], plain: string[] = [], ids = new Set<string>()
  let active: Omit<CourseBlock, 'text'> | null = null, body: string[] = [], fence: string | null = null
  function flush() { if (plain.length) { blocks.push({ type: 'markdown', text: plain.join('\n') }); plain.length = 0 } }
  for (const line of markdown.split(/\r?\n/)) {
    const text = line.trim()
    if (text.startsWith('```') || text.startsWith('~~~')) {
      const marker = text.slice(0, 3)
      fence = fence === marker ? null : fence === null ? marker : fence
    }
    if (fence === null && text.startsWith(':::')) {
      if (text === ':::') {
        if (!active) throw new Error('Unmatched course block closing tag.')
        blocks.push({ ...active, text: body.join('\n').trim() }); active = null; body = []; continue
      }
      if (active) throw new Error('Nested course blocks are not supported.')
      flush()
      const [tag, id, extra] = text.slice(3).trim().split(/\s+/)
      if (!tags.has(tag) || extra || (tag === 'exercise' ? !id : !!id)) throw new Error('Unknown course block tag.')
      if (tag === 'exercise') {
        if (!/^[a-z][a-z0-9_]{0,39}$/.test(id) || ids.has(id)) throw new Error('Invalid exercise marker.')
        ids.add(id)
      }
      active = { type: tag as CourseBlock['type'], ...(id ? { id } : {}) }
    } else if (active) body.push(line)
    else plain.push(line)
  }
  if (active) throw new Error('Unclosed course block.')
  flush()
  return blocks
}

export function exerciseBinding(text: string) {
  let fence: string | null = null, offset = 0
  let binding: { itemId: string; index: number; length: number } | null = null
  for (const line of text.split('\n')) {
    const trimmed = line.trim()
    if (trimmed.startsWith('```') || trimmed.startsWith('~~~')) {
      const marker = trimmed.slice(0, 3)
      fence = fence === marker ? null : fence === null ? marker : fence
    } else if (fence === null) {
      for (const match of line.matchAll(/\{\{item:([0-9a-f-]{36})\}\}/g)) {
        if (binding) return null
        binding = { itemId: match[1], index: offset + match.index, length: match[0].length }
      }
    }
    offset += line.length + 1
  }
  // Slice the actual binding by position: an identical marker in a fenced
  // example must survive, along with its original text and line endings.
  return binding ? { itemId: binding.itemId, text: (text.slice(0, binding.index) + text.slice(binding.index + binding.length)).trim() } : null
}
