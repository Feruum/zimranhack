"""Private learning text must not leave the process through error reporting."""
import json
import unittest

from src.services.thinktogether.telemetry import scrub_thinktogether_event


class ThinkTogetherTelemetryTests(unittest.TestCase):
    def private_event(self):
        secret = 'PRIVATE-LEARNER-CANARY'
        return {
            'event_id': 'operational-event', 'environment': 'production',
            'request': {'url': 'https://example.com/api/v1/tt/attempts',
                        'data': {'response': secret}, 'headers': {'authorization': secret},
                        'cookies': secret, 'query_string': 'answer='+secret},
            'user': {'email': secret}, 'extra': {'cognitive_profile': secret},
            'message': secret, 'logentry': {'message': secret, 'formatted': secret},
            'transaction': '/api/v1/tt/attempts?answer='+secret,
            'tags': {'model': 'approved-model', 'status_code': 503, 'transcript': secret},
            'contexts': {'trace': {'trace_id': 'a'*32, 'span_id': 'b'*16,
                                   'status': 'internal_error', 'description': secret},
                         'learner': {'answer': secret}},
            'breadcrumbs': [{'message': secret, 'data': {'transcript': secret}}],
            'spans': [{'span_id': 'c'*16, 'op': 'http.client',
                       'description': secret, 'data': {'answer': secret}}],
            'exception': {'values': [{'type': 'ProviderUnavailable', 'value': secret,
                'stacktrace': {'frames': [{'filename': 'src/services/thinktogether/learning.py',
                                          'function': 'submit_attempt', 'lineno': 42,
                                          'vars': {'response': secret},
                                          'context_line': secret, 'pre_context': [secret]}]}}]},
        }

    def test_private_payload_is_removed_while_operational_location_survives(self):
        event = scrub_thinktogether_event(self.private_event())
        self.assertNotIn('PRIVATE-LEARNER-CANARY', json.dumps(event))
        self.assertEqual(event['exception']['values'][0]['type'], 'ProviderUnavailable')
        frame = event['exception']['values'][0]['stacktrace']['frames'][0]
        self.assertEqual(frame['filename'], 'src/services/thinktogether/learning.py')
        self.assertEqual(frame['function'], 'submit_attempt')
        self.assertEqual(frame['lineno'], 42)
        self.assertEqual(event['contexts']['trace']['trace_id'], 'a'*32)
        self.assertEqual(event['tags'], {'model': 'approved-model', 'status_code': 503})

    def test_worker_exception_is_private_without_an_http_request(self):
        event = self.private_event()
        event.pop('request')
        event.pop('transaction')
        self.assertNotIn('PRIVATE-LEARNER-CANARY', json.dumps(scrub_thinktogether_event(event)))

    def test_worker_log_and_thread_stack_are_detected(self):
        for marker in (
            {'logger': 'src.services.thinktogether.jobs_worker'},
            {'threads': {'values': [{'stacktrace': {'frames': [
                {'filename': '/app/api/src/services/thinktogether/facilitator.py'}]}}]}},
            {'transaction': 'thinktogether.learning.grade'},
            {'transaction': 'tt.learning.grade'},
        ):
            event = dict(marker, logentry={'message': 'PRIVATE-LEARNER-CANARY'})
            self.assertNotIn('PRIVATE-LEARNER-CANARY', json.dumps(scrub_thinktogether_event(event)))

    def test_non_thinktogether_event_is_unchanged(self):
        event = {'request': {'url': 'https://example.com/api/v1/courses'},
                 'extra': {'ordinary': 'retained'}, 'message': 'ordinary error'}
        self.assertIs(scrub_thinktogether_event(event), event)
        self.assertEqual(event['extra'], {'ordinary': 'retained'})

    def test_transaction_payload_and_null_events_are_safe(self):
        event = {'transaction': '/orgs/default/thinktogether/practice',
                 'type': 'transaction', 'contexts': {'trace': {'span_id': 'c'*16}},
                 'spans': [{'description': 'PRIVATE-LEARNER-CANARY',
                            'data': {'reasoning': 'PRIVATE-LEARNER-CANARY'}}]}
        self.assertNotIn('PRIVATE-LEARNER-CANARY', json.dumps(scrub_thinktogether_event(event)))
        self.assertIsNone(scrub_thinktogether_event(None))


if __name__ == '__main__':
    unittest.main()
