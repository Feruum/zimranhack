import { describe, expect, it } from 'bun:test'
import { activityHref, attemptSummary, checkoutTarget, mergeRoomEvents, responseFor, responseLabel, scheduleLabel } from '../services/thinktogether/helpers'
import { supportRequest } from '../services/thinktogether/support'

const base = { id: 'item-1', title: 'Task', prompt: 'Choose', kind: 'single_choice', choices: [{ id: 'a', text: 'First' }, { id: 'b', text: 'Second' }], estimated_minutes: 5 }
const empty = { choice: '', choices: [], value: '', text: '', matches: {} }
describe('assessment response boundary', () => {
  it('requires a supplied valid single choice', () => {
    expect(() => responseFor(base, empty)).toThrow()
    expect(() => responseFor(base, { ...empty, choice: 'invented' })).toThrow()
    expect(responseFor(base, { ...empty, choice: 'a' })).toEqual({ choice: 'a' })
  })
  it('rejects absent multiple responses and sends unique allowed choices', () => {
    const item = { ...base, kind: 'multiple_choice' }
    expect(() => responseFor(item, empty)).toThrow()
    expect(responseFor(item, { ...empty, choices: ['b', 'a', 'b'] })).toEqual({ choices: ['b', 'a'] })
    expect(() => responseFor(item, { ...empty, choices: ['x'] })).toThrow()
  })
  it('preserves a valid zero and rejects non-finite or blank numbers', () => {
    const item = { ...base, kind: 'numeric' }
    expect(responseFor(item, { ...empty, value: '0' })).toEqual({ value: 0 })
    for (const value of ['', ' ', 'Infinity', 'NaN', '1e9999']) expect(() => responseFor(item, { ...empty, value })).toThrow()
  })
  it('trims reasoning and enforces its bound', () => {
    const item = { ...base, kind: 'reasoning' }
    expect(responseFor(item, { ...empty, text: '  Evidence first.  ' })).toEqual({ text: 'Evidence first.' })
    expect(() => responseFor(item, { ...empty, text: ' '.repeat(5) })).toThrow()
    expect(() => responseFor(item, { ...empty, text: 'a'.repeat(20001) })).toThrow()
  })
  it('requires a complete one-to-one matching response without author scoring data', () => {
    const item = { ...base, kind: 'matching', choices: [{ id: 'l1', text: 'Risk', side: 'left' }, { id: 'l2', text: 'Issue', side: 'left' }, { id: 'r1', text: 'Uncertain', side: 'right' }, { id: 'r2', text: 'Observed', side: 'right' }] }
    expect(() => responseFor(item, empty)).toThrow()
    expect(() => responseFor(item, { ...empty, matches: { l1: 'r1', l2: 'r1' } })).toThrow()
    const response = responseFor(item, { ...empty, matches: { l1: 'r1', l2: 'r2', injected: 'r1' } })
    expect(response).toEqual({ matches: { l1: 'r1', l2: 'r2' } })
    expect(responseLabel(item, response)).toBe('Risk → Uncertain; Issue → Observed')
  })
})
describe('persisted workspace navigation and replay', () => {
  it('describes withheld cognitive results as saved assessment responses', () => {
    expect(attemptSummary({ phase: 'diagnostic', assessment_kind: 'cognitive', score: null })).toBe('Assessment response saved')
    expect(attemptSummary({ phase: 'diagnostic', assessment_kind: 'domain', score: null })).toBe('Awaiting evaluation')
    expect(attemptSummary({ phase: 'independent', score: 0 })).toBe('0% criterion score')
  })
  it('routes planned lessons, skill practice and due reviews to the correct flow', () => {
    expect(activityHref({ kind: 'lesson', lesson_id: 'lesson-id', minutes: 10, reason: '' })).toBe('/thinktogether/program?lesson=lesson-id')
    expect(activityHref({ kind: 'review', review_id: 'review-id', skill_id: 'skill-id', minutes: 5, reason: '' })).toBe('/thinktogether/practice?review=review-id')
    expect(activityHref({ kind: 'practice', skill_id: 'skill-id', minutes: 5, reason: '' })).toBe('/thinktogether/practice?skill=skill-id')
  })
  it('deduplicates SSE replay events and keeps the sequence order', () => {
    const chat = { id: 2, kind: 'chat', payload: { text: 'Reasoning' } }
    expect(mergeRoomEvents([chat], [{ id: 1, kind: 'advance', payload: { phase: 'discussion' } }, chat]).map(e => e.id)).toEqual([1, 2])
  })
  it('formats Monday-zero cohort schedules without guessing a time', () => {
    expect(scheduleLabel({ weekday: 0, local_time: '18:00' }, 'UTC')).toBe('Monday · 18:00 · UTC')
    expect(scheduleLabel({}, 'UTC')).toBe('Schedule pending · UTC')
  })
  it('only allows the trusted payment provider over https', () => {
    expect(checkoutTarget('https://polar.sh/checkout/test')).toBe('https://polar.sh/checkout/test')
    for (const url of ['javascript:alert(1)', 'https://polar.sh.evil.test/', 'http://polar.sh/checkout', '/local', 'https://evil.test/']) expect(() => checkoutTarget(url)).toThrow()
  })
})
describe('support request boundary', () => {
  it('trims learner text and links a selected response using the supplied retry key', () => {
    expect(supportRequest({ kind: 'assessment', title: '  Review request  ', message: '  Please review my reasoning.  ', attempt_id: 'attempt-id' }, 'retry-key')).toEqual({ kind: 'assessment', title: 'Review request', message: 'Please review my reasoning.', attempt_id: 'attempt-id', idempotency_key: 'retry-key' })
  })
  it('omits an unselected response and rejects empty or oversized requests', () => {
    const values = { kind: 'learning', title: 'A question', message: 'Can you help?', attempt_id: '' }
    expect(supportRequest(values, 'retry-key')).not.toHaveProperty('attempt_id')
    expect(() => supportRequest({ ...values, kind: 'admin' }, 'retry-key')).toThrow()
    expect(() => supportRequest({ ...values, title: ' ' }, 'retry-key')).toThrow()
    expect(() => supportRequest({ ...values, title: 'a'.repeat(161) }, 'retry-key')).toThrow()
    expect(() => supportRequest({ ...values, message: ' ' }, 'retry-key')).toThrow()
    expect(() => supportRequest({ ...values, message: 'a'.repeat(6001) }, 'retry-key')).toThrow()
    expect(() => supportRequest(values, '')).toThrow()
  })
})
