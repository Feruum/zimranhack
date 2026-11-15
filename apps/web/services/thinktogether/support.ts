export const SUPPORT_KINDS = ['learning', 'group', 'billing', 'assessment'] as const
export type SupportKind = typeof SUPPORT_KINDS[number]
export type SupportStatus = 'open' | 'in_progress' | 'resolved'
export interface SupportTicket {
  id: string
  kind: SupportKind | 'item_shortage'
  title: string
  status: SupportStatus
  attempt_id: string | null
  created_at: string
  messages: { id: string; author: 'learner' | 'staff' | 'system'; message: string; created_at: string }[]
  details?: Record<string, unknown>
}
export function supportRequest(values: { kind: string; title: string; message: string; attempt_id?: string }, idempotencyKey: string) {
  if (!SUPPORT_KINDS.includes(values.kind as SupportKind)) throw new Error('Choose a support topic.')
  const title = values.title.trim()
  const message = values.message.trim()
  if (!title || title.length > 160) throw new Error('Enter a title of up to 160 characters.')
  if (!message || message.length > 6000) throw new Error('Enter a message of up to 6,000 characters.')
  if (!idempotencyKey) throw new Error('Please try submitting your request again.')
  return { kind: values.kind as SupportKind, title, message, idempotency_key: idempotencyKey, ...(values.attempt_id ? { attempt_id: values.attempt_id } : {}) }
}
