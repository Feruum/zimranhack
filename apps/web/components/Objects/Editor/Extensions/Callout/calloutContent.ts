import { normalizeEmptyEditorDocument } from '../../documentContent'

// Callouts hold paragraphs and lists. Older callouts stored their text
// directly (`content: 'text*'`), so their saved JSON must be wrapped in a
// paragraph before it reaches an editor using the current schema.
export const CALLOUT_CONTENT = '(paragraph | bulletList | orderedList)+'

const CALLOUT_BLOCK_TYPES = new Set(['paragraph', 'bulletList', 'orderedList'])

function normalizeCalloutChildren(children: any[]): any[] {
  const blocks: any[] = []
  let inlineRun: any[] = []
  const flushInline = () => {
    if (inlineRun.length > 0) {
      blocks.push({ type: 'paragraph', content: inlineRun })
      inlineRun = []
    }
  }
  for (const child of children) {
    if (!child || typeof child !== 'object') continue
    if (CALLOUT_BLOCK_TYPES.has(child.type)) {
      flushInline()
      blocks.push(normalizeCalloutNode(child))
    } else {
      inlineRun.push(child)
    }
  }
  flushInline()
  return blocks.length > 0 ? blocks : [{ type: 'paragraph' }]
}

function normalizeCalloutNode<T = any>(content: T): T {
  if (Array.isArray(content)) {
    return content.map((node) => normalizeCalloutNode(node)) as T
  }
  if (!content || typeof content !== 'object') return content

  const node: any = content
  if (node.type === 'callout') {
    return {
      ...node,
      content: normalizeCalloutChildren(Array.isArray(node.content) ? node.content : []),
    }
  }
  if (Array.isArray(node.content)) {
    return { ...node, content: normalizeCalloutNode(node.content) }
  }
  return content
}

export function normalizeCalloutContent<T = any>(content: T): T {
  return normalizeCalloutNode(normalizeEmptyEditorDocument(content))
}
