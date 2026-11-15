import type { JSONContent } from '@tiptap/core'

export function createEmptyEditorDocument(): JSONContent {
  return { type: 'doc', content: [{ type: 'paragraph' }] }
}

// Legacy empty activities store {}. Apply this only at the document boundary;
// empty attributes or nested nodes must not become nested documents.
export function normalizeEmptyEditorDocument<T>(content: T): T {
  if (
    content &&
    typeof content === 'object' &&
    !Array.isArray(content) &&
    Object.keys(content).length === 0
  ) {
    return createEmptyEditorDocument() as T
  }
  return content
}
