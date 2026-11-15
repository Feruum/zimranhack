import { describe, expect, it } from 'bun:test'
import { scrubThinkTogetherEvent, telemetryCaptureOptions } from '../services/thinktogether/telemetry.ts'
import { scrubSentryEvent } from '../services/utils/ts/scrubUrl.ts'

const canary = 'PRIVATE-LEARNER-CANARY'
function privateEvent() {
  return {
    event_id: 'operational-event', environment: 'production',
    request: { url: 'https://example.com/api/v1/tt/attempts', data: { response: canary },
      headers: { authorization: canary }, cookies: canary },
    user: { email: canary }, extra: { cognitive_profile: canary }, message: canary,
    logentry: { message: canary }, transaction: '/orgs/default/thinktogether/practice',
    contexts: { trace: { trace_id: 'a'.repeat(32), span_id: 'b'.repeat(16), description: canary },
      learner: { answer: canary } },
    tags: { model: 'approved-model', status_code: 503, transcript: canary },
    breadcrumbs: [{ message: canary, data: { transcript: canary } }],
    spans: [{ op: 'http.client', description: canary, data: { answer: canary } }],
    exception: { values: [{ type: 'ProviderUnavailable', value: canary, stacktrace: { frames: [{
      filename: '/services/thinktogether/api.ts', function: 'ttApi', lineno: 42,
      vars: { response: canary }, context_line: canary, pre_context: [canary],
    }] } }] },
  }
}

describe('ThinkTogether error reporting privacy', () => {
  it('removes learner text from all event payload channels and keeps safe diagnostics', () => {
    const result = scrubSentryEvent(privateEvent())
    expect(JSON.stringify(result)).not.toContain(canary)
    expect(result.exception.values[0].type).toBe('ProviderUnavailable')
    expect(result.exception.values[0].stacktrace.frames[0]).toEqual({
      filename: '/services/thinktogether/api.ts', function: 'ttApi', lineno: 42,
    })
    expect(result.contexts.trace.trace_id).toBe('a'.repeat(32))
    expect(result.tags).toEqual({ model: 'approved-model', status_code: 503 })
  })
  it('protects background service frames with no request or transaction', () => {
    const event = privateEvent()
    delete event.request
    delete event.transaction
    expect(JSON.stringify(scrubThinkTogetherEvent(event))).not.toContain(canary)
  })
  it('protects transaction traces and never retains span descriptions or data', () => {
    const event = { transaction: '/orgs/default/thinktogether/profile', type: 'transaction',
      spans: [{ description: canary, data: { cognitive_profile: canary } }] }
    expect(JSON.stringify(scrubSentryEvent(event))).not.toContain(canary)
    expect(JSON.stringify(scrubSentryEvent({ transaction: 'tt.learning.grade',
      logentry: { message: canary } }))).not.toContain(canary)
  })
  it('preserves ordinary events and existing auth URL redaction', () => {
    const ordinary = { request: { url: 'https://example.com/courses' }, extra: { ordinary: 'kept' } }
    expect(scrubThinkTogetherEvent(ordinary)).toBe(ordinary)
    const auth = scrubSentryEvent({ request: { url: 'https://example.com/login?token=secret&mode=login' }, extra: { ordinary: 'kept' } })
    expect(auth.request.url).not.toContain('secret')
    expect(auth.request.url).toContain('mode=login')
    expect(auth.extra.ordinary).toBe('kept')
  })
  it('never constructs browser replay when ThinkTogether is enabled', () => {
    const options = telemetryCaptureOptions(true, () => { throw new Error('Replay must not initialize') })
    expect(options.replaysSessionSampleRate).toBe(0)
    expect(options.replaysOnErrorSampleRate).toBe(0)
    expect(options.integrations).toEqual([])
    expect(options.sendDefaultPii).toBe(false)
  })
  it('retains the existing replay policy for other deployments', () => {
    const integration = { name: 'Replay' }
    expect(telemetryCaptureOptions(false, () => integration).integrations).toEqual([integration])
    expect(telemetryCaptureOptions(false, () => integration).replaysOnErrorSampleRate).toBe(.1)
  })
})
