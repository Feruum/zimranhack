"""Allow only operational diagnostics for private learning telemetry."""
import re


def _frames(event):
    for section in ('exception', 'threads'):
        for value in (event.get(section) or {}).get('values', []):
            yield from (value.get('stacktrace') or {}).get('frames', [])


def _private(event):
    markers = [(event.get('request') or {}).get('url', ''),
               event.get('transaction', ''), event.get('logger', '')]
    markers.extend(str(frame.get('filename', ''))+' '+str(frame.get('module', ''))
                   for frame in _frames(event))
    return any(re.search(r'thinktogether|(?:^|[/.:\s])tt(?:[/.:\s]|$|\?)',
                         str(marker), re.I) for marker in markers)


def _pick(value, keys):
    return {key: value[key] for key in keys
            if key in value and isinstance(value[key], (str, int, float, bool))}


def _frame(frame):
    safe = _pick(frame, ('filename', 'function', 'module', 'lineno', 'colno', 'in_app'))
    if isinstance(safe.get('filename'), str):
        safe['filename'] = safe['filename'].split('?', 1)[0].split('#', 1)[0]
    return safe


def scrub_thinktogether_event(event, hint=None):
    """Return a fresh allowlisted event; ordinary application events pass through.

    Do not inspect or log exception messages, locals, responses, assessment
    profiles, transcripts, or breadcrumb payloads. Worker frames and logger
    names identify background learning jobs without an HTTP request.
    """
    if not event or not _private(event):
        return event
    safe = _pick(event, ('event_id', 'timestamp', 'start_timestamp', 'platform',
                         'level', 'environment', 'release', 'dist', 'type'))
    trace = (event.get('contexts') or {}).get('trace')
    if isinstance(trace, dict):
        safe['contexts'] = {'trace': _pick(trace, ('trace_id', 'span_id', 'parent_span_id',
                                                  'op', 'status', 'origin'))}
    tags = event.get('tags')
    if isinstance(tags, dict):
        safe['tags'] = _pick(tags, ('model', 'status', 'status_code', 'environment'))
    values = (event.get('exception') or {}).get('values', [])
    if values:
        safe['exception'] = {'values': [dict(
            _pick(value, ('type',)),
            stacktrace={'frames': [_frame(frame) for frame in
                                  (value.get('stacktrace') or {}).get('frames', [])]},
        ) for value in values]}
    if isinstance(event.get('spans'), list):
        safe['spans'] = [_pick(span, ('trace_id', 'span_id', 'parent_span_id',
                                      'op', 'status', 'start_timestamp', 'timestamp'))
                         for span in event['spans'] if isinstance(span, dict)]
    return safe
