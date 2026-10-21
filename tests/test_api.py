# The API module against the local stand-in: requests, headers, polling, every error shape. No real API calls.
import os
import sys
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib import api  # noqa: E402
from mock import AI_TEXT, FIXTURES, ODD, MockApi  # noqa: E402


class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mock = MockApi()
        cls.real = (api.API, api.POLL_SECONDS, api.MAX_POLLS)
        api.API = cls.mock.url
        api.POLL_SECONDS = 0.01

    @classmethod
    def tearDownClass(cls):
        cls.mock.stop()
        api.API, api.POLL_SECONDS, api.MAX_POLLS = cls.real

    def setUp(self):
        self.mock.queue = []
        self.mock.requests = []
        api.MAX_POLLS = 150

    def test_detect(self):
        run, free = api.run_skill('detect', AI_TEXT)
        self.assertEqual(run['output']['ai'], 0.96)
        self.assertEqual(free, 9)
        method, path, headers, body = self.mock.requests[0]
        self.assertEqual((method, path), ('POST', '/v1/detect'))
        self.assertEqual(body, {'text': AI_TEXT})

    def test_headers_name_the_client(self):
        api.run_skill('detect', AI_TEXT)
        headers = self.mock.requests[0][2]
        self.assertEqual(headers['X-P3N-Client'], 'p3n-sublime/' + api.VERSION)
        self.assertEqual(headers['Content-Type'], 'application/json')
        self.assertEqual(headers['Accept'], 'application/json')
        self.assertNotIn('Authorization', headers)
        self.assertLessEqual(len(api.CLIENT), 40)  # the API's limit for X-P3N-Client

    def test_key_is_a_bearer_token(self):
        run, free = api.run_skill('humanize', AI_TEXT, key='p3n_abc', options={'fresh': True})
        method, path, headers, body = self.mock.requests[0]
        self.assertEqual(headers['Authorization'], 'Bearer p3n_abc')
        self.assertEqual(body, {'text': AI_TEXT, 'options': {'fresh': True}})
        self.assertIsNone(free)  # keyed calls have no free count

    def test_unicode_round_trip(self):
        text = 'Привет, мир. Ünïcödé “quotes” and an emoji 🙂 in a sentence long enough.'
        api.run_skill('detect', text)
        self.assertEqual(self.mock.requests[0][3]['text'], text)

    def test_202_is_polled(self):
        self.mock.queue = [{'status': 202, 'body': dict(FIXTURES['detect'], status='queued', output=None)},
                           {'body': dict(FIXTURES['detect'], status='running', output=None)}]
        run, _ = api.run_skill('detect', AI_TEXT)
        self.assertEqual(run['status'], 'succeeded')
        self.assertEqual([r[1] for r in self.mock.requests], ['/v1/detect', '/v1/runs/mock-detect', '/v1/runs/mock-detect'])

    def test_a_dropped_poll_keeps_polling(self):
        self.mock.queue = [{'status': 202, 'body': dict(FIXTURES['detect'], status='queued', output=None)}, {'drop': True}]
        run, _ = api.run_skill('detect', AI_TEXT)
        self.assertEqual(run['output']['ai'], 0.96)
        self.assertEqual(len(self.mock.requests), 3)

    def test_still_running_after_the_polls(self):
        api.MAX_POLLS = 2
        queued = {'status': 202, 'body': dict(FIXTURES['detect'], status='queued', output=None)}
        self.mock.queue = [queued, queued, queued]
        with self.assertRaises(api.ApiError) as c:
            api.run_skill('detect', AI_TEXT)
        self.assertEqual(c.exception.type, 'still_running')
        self.assertIn('may still finish and be charged', c.exception.message)

    def test_cancel_stops_polling(self):
        cancel = threading.Event()
        cancel.set()
        self.mock.queue = [{'status': 202, 'body': dict(FIXTURES['detect'], status='queued', output=None)}]
        with self.assertRaises(api.Cancelled):
            api.run_skill('detect', AI_TEXT, cancel=cancel)
        self.assertEqual(len(self.mock.requests), 1)

    def test_free_limit(self):
        self.mock.queue = [ODD[0][2]]
        with self.assertRaises(api.ApiError) as c:
            api.run_skill('detect', AI_TEXT)
        e = c.exception
        self.assertEqual((e.status, e.type, e.retry_after, e.free), (429, 'free_limit', 600, 0))
        self.assertEqual(e.signup_url, 'javascript:alert(1)')  # kept as sent; core.signup_link refuses it

    def test_api_message_is_kept(self):
        self.mock.queue = [ODD[1][2]]
        with self.assertRaises(api.ApiError) as c:
            api.run_skill('humanize', AI_TEXT)
        self.assertEqual(c.exception.type, 'too_long')
        self.assertIn('send a shorter text', c.exception.message)

    def test_html_error_page(self):
        self.mock.queue = [ODD[2][2]]
        with self.assertRaises(api.ApiError) as c:
            api.run_skill('detect', AI_TEXT)
        self.assertEqual((c.exception.type, c.exception.message), ('upstream_error', 'p3n answered 502.'))

    def test_failed_run(self):
        self.mock.queue = [ODD[3][2]]
        with self.assertRaises(api.ApiError) as c:
            api.run_skill('humanize', AI_TEXT)
        self.assertEqual(c.exception.type, 'run_failed')
        self.assertIn('provider failed', c.exception.message)

    def test_type_from_status(self):
        self.mock.queue = [{'status': 402, 'body': {}}]
        with self.assertRaises(api.ApiError) as c:
            api.run_skill('detect', AI_TEXT, key='p3n_abc')
        self.assertEqual(c.exception.type, 'insufficient_credit')

    def test_retry_after_header(self):
        self.mock.queue = [{'status': 429, 'body': {'error': {'type': 'rate_limited', 'message': 'Slow down.'}},
                            'headers': {'Retry-After': '30'}}]
        with self.assertRaises(api.ApiError) as c:
            api.run_skill('detect', AI_TEXT, key='p3n_abc')
        self.assertEqual((c.exception.type, c.exception.retry_after), ('rate_limited', 30))

    def test_network_error(self):
        api.API = 'http://127.0.0.1:9'  # nothing listens there
        try:
            with self.assertRaises(api.ApiError) as c:
                api.run_skill('detect', AI_TEXT)
        finally:
            api.API = self.mock.url
        self.assertEqual((c.exception.status, c.exception.type), (0, 'network'))
        self.assertEqual(c.exception.message, api.NETWORK)

    def test_me(self):
        self.assertEqual(api.me('p3n_good')['email'], 'you@example.com')
        with self.assertRaises(api.ApiError) as c:
            api.me('p3n_wrong')
        self.assertEqual(c.exception.type, 'unauthorized')

    def test_ssl_context_verifies(self):
        import ssl
        ctx = api.ssl_context()
        self.assertEqual(ctx.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(ctx.check_hostname)

    def test_defaults(self):
        self.assertEqual(self.real[0], 'https://api.p3n.si')
        self.assertEqual(api.MAX_CHARS, {'free': 5000, 'key': 200000})
        self.assertEqual(api.FREE_PER_HOUR, 10)


if __name__ == '__main__':
    unittest.main()
