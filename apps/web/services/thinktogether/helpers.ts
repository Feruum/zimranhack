import type { Activity, Attempt, Item, ResponseValue, RoomEvent, Schedule } from './types'

export function responseFor(item: Item, values: { choice: string; choices: string[]; value: string; text: string; matches?: Record<string, string> }): ResponseValue {
  const allowed = new Set(item.choices.map(c => c.id))
  if (item.kind === 'single_choice') {
    if (!allowed.has(values.choice)) throw new Error('Choose one answer.')
    return { choice: values.choice }
  }
  if (item.kind === 'multiple_choice') {
    const choices = Array.from(new Set(values.choices))
    if (!choices.length || choices.some(c => !allowed.has(c))) throw new Error('Choose at least one available answer.')
    return { choices }
  }
  if (item.kind === 'numeric') {
    if (!values.value.trim() || !Number.isFinite(Number(values.value))) throw new Error('Enter a finite number.')
    return { value: Number(values.value) }
  }
  if (item.kind === 'matching') {
    const left = item.choices.filter(c => c.side === 'left')
    const right = new Set(item.choices.filter(c => c.side === 'right').map(c => c.id))
    const matches = values.matches || {}
    if (!left.length || left.some(c => !right.has(matches[c.id]))) throw new Error('Choose a match for every item.')
    if (new Set(Object.values(matches)).size !== left.length) throw new Error('Use each match only once.')
    return { matches: Object.fromEntries(left.map(c => [c.id, matches[c.id]])) }
  }
  if (!values.text.trim() || values.text.length > 20000) throw new Error('Enter your reasoning in 1–20,000 characters.')
  return { text: values.text.trim() }
}
export function responseLabel(item: Item, response: ResponseValue) {
  const label = (id: string) => item.choices.find(c => c.id === id)?.text || id
  if ('choice' in response) return label(response.choice)
  if ('choices' in response) return response.choices.map(label).join('; ')
  if ('value' in response) return String(response.value)
  if ('text' in response) return response.text
  return Object.entries(response.matches).map(([left, right]) => `${label(left)} → ${label(right)}`).join('; ')
}
export const humanize = (value: string) => value.replace(/_/g, ' ').replace(/^./, c => c.toUpperCase())
export function attemptSummary(attempt: Pick<Attempt, 'phase' | 'score' | 'assessment_kind'>) {
  if (attempt.assessment_kind === 'cognitive') return 'Assessment response saved'
  if (attempt.phase === 'preview') return 'Sample preview'
  if (attempt.score === null) return 'Awaiting evaluation'
  return `${Math.round(attempt.score * 100)}% criterion score`
}
export function activityHref(activity: Activity) {
  if (activity.kind === 'lesson') return `/thinktogether/program?lesson=${encodeURIComponent(activity.lesson_id || '')}`
  const params = new URLSearchParams()
  if (activity.review_id) params.set('review', activity.review_id)
  else if (activity.skill_id) params.set('skill', activity.skill_id)
  return `/thinktogether/practice${params.size ? `?${params}` : ''}`
}
export function mergeRoomEvents(existing: RoomEvent[], incoming: RoomEvent[]) {
  return Array.from(new Map([...existing, ...incoming].map(event => [event.id, event])).values()).sort((a, b) => a.id - b.id)
}
export const PHASES = ['preparation', 'individual_attempt', 'discussion', 'transfer', 'summary'] as const
export function scheduleLabel(schedule: Schedule, timezone: string) {
  const days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
  return `${schedule.weekday === undefined ? 'Schedule pending' : days[schedule.weekday]}${schedule.local_time ? ` · ${schedule.local_time}` : ''} · ${timezone}`
}
export function formatDate(value: string, timezone?: string) {
  if (!value) return 'Not scheduled'
  const date = new Date(value.length === 10 ? `${value}T12:00:00Z` : value)
  if (Number.isNaN(date.getTime())) return 'Date unavailable'
  return new Intl.DateTimeFormat('en', { dateStyle: 'medium', ...(value.length > 10 ? { timeStyle: 'short' as const } : {}), ...(value.length === 10 ? { timeZone: 'UTC' } : timezone ? { timeZone: timezone } : {}) }).format(date)
}
export function checkoutTarget(value: string) {
  const target = new URL(value)
  if (target.protocol !== 'https:' || !['polar.sh', 'sandbox.polar.sh', 'buy.polar.sh', 'checkout.polar.sh'].includes(target.hostname)) throw new Error('The payment link could not be verified. Please try again.')
  return target.href
}
