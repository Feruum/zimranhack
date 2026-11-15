/** Learning answers, cognitive profiles and transcripts never belong in telemetry. */
type TelemetryEvent = Record<string, any>

function pick(value: TelemetryEvent, keys: string[]) {
  return Object.fromEntries(keys.filter(key => ['string', 'number', 'boolean'].includes(typeof value[key]))
    .map(key => [key, value[key]]))
}

function frames(event: TelemetryEvent): TelemetryEvent[] {
  return ['exception', 'threads'].flatMap(section => (event[section]?.values || [])
    .flatMap((value: TelemetryEvent) => value.stacktrace?.frames || []))
}

function isPrivate(event: TelemetryEvent) {
  const markers = [event.request?.url, event.transaction, event.logger,
    ...frames(event).map(frame => `${frame.filename || ''} ${frame.module || ''}`)]
  return markers.some(marker => typeof marker === 'string' && /thinktogether|(?:^|[/.:\s])tt(?:[/.:\s]|$|\?)/i.test(marker))
}

export function scrubThinkTogetherEvent<E extends TelemetryEvent | null>(event: E): E {
  if (!event || !isPrivate(event)) return event
  const safe: TelemetryEvent = pick(event, ['event_id', 'timestamp', 'start_timestamp', 'platform',
    'level', 'environment', 'release', 'dist', 'type'])
  if (event.contexts?.trace) safe.contexts = { trace: pick(event.contexts.trace,
    ['trace_id', 'span_id', 'parent_span_id', 'op', 'status', 'origin']) }
  if (event.tags) safe.tags = pick(event.tags, ['model', 'status', 'status_code', 'environment'])
  if (event.exception?.values) safe.exception = { values: event.exception.values.map((value: TelemetryEvent) => ({
    ...pick(value, ['type']), stacktrace: { frames: (value.stacktrace?.frames || []).map((frame: TelemetryEvent) => {
      const location = pick(frame, ['filename', 'function', 'module', 'lineno', 'colno', 'in_app'])
      if (typeof location.filename === 'string') location.filename = location.filename.split(/[?#]/, 1)[0]
      return location
    }) },
  })) }
  if (Array.isArray(event.spans)) safe.spans = event.spans.map((span: TelemetryEvent) => pick(span,
    ['trace_id', 'span_id', 'parent_span_id', 'op', 'status', 'start_timestamp', 'timestamp']))
  return safe as E
}

/** Lazy replay construction prevents the replay integration loading in TT mode. */
export function telemetryCaptureOptions<T>(thinkTogether: boolean, replay: () => T) {
  return {
    sendDefaultPii: false,
    replaysSessionSampleRate: 0,
    replaysOnErrorSampleRate: thinkTogether ? 0 : .1,
    integrations: thinkTogether ? [] : [replay()],
  }
}
