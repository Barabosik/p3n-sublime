# A stand-in for api.p3n.si on 127.0.0.1, with the canned runs the other p3n clients test against (the Chrome
# extension's dev/shared.mjs). The unit tests and the in-editor checks (dev/) both use it; neither calls the real API.
import copy
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

AI_TEXT = ("In today's fast-paced world, it's worth noting that effective communication is more important than ever. "
           'Moreover, leveraging the right tools can help teams unlock their full potential and navigate the complexities of modern work.')
SHORT = ('Ultimately, this seamless transition unlocks a rich tapestry of possibilities for teams of all sizes, '
         'empowering everyone to thrive in an ever-evolving landscape.')

FIXTURES = {
    'detect': {'id': 'mock-detect', 'skill': 'detect', 'status': 'succeeded', 'cached': False, 'error': None, 'charged_usd': '0',
               'output': {'version': 'detect-v1.2', 'ai': 0.96, 'label': 'ai',
                          'verdict': 'Generic filler phrases with no specific claim or example.',
                          'confidence': {'level': 'medium', 'reason': 'Two short sentences.'},
                          'sentences': [{'text': AI_TEXT.split('. ')[0] + '.', 'why': 'A stock opening with a vague claim.'}],
                          'examples': []}},
    # The live API ends the rewrite with a line break; the package must not carry it into the buffer.
    'humanize': {'id': 'mock-humanize', 'skill': 'humanize', 'status': 'succeeded', 'cached': False, 'error': None, 'charged_usd': '0',
                 'output': {'version': 'humanize-v1.3', 'model': 'haiku', 'text': 'This transition helps teams of all sizes.\n',
                            'ai_before': 0.95, 'ai_after': 0.01, 'label_after': 'human', 'passes': True, 'invented_blocked': []}},
    'me': {'email': 'you@example.com', 'balance_usd': '9.998000', 'key': {'name': 'laptop', 'prefix': 'a1b2c3d4e5f6'}},
}

# What api.p3n.si answered for AI_TEXT and SHORT on 2026-10-21 (the e2e's two live calls), for screenshots that show
# real answers without spending more of the free hour.
LIVE = {
    'detect': {'id': 'live-detect', 'skill': 'detect', 'status': 'succeeded', 'cached': False, 'error': None, 'charged_usd': '0',
               'output': {'version': 'detect-v1.4', 'ai': 0.96, 'label': 'ai',
                          'verdict': ('This reads like generic model-generated copy, with several stock phrases packed into two sentences and no '
                                      'specific information. There isn’t enough evidence to tell whether it was later edited or humanized.'),
                          'confidence': {'level': 'high', 'reason': 'The phrasing is unusually dense with familiar AI-style filler.'},
                          'sentences': [{'text': AI_TEXT.split('. ')[0] + '.', 'why': 'Combines two common filler openings with a broad, unsupported claim.'},
                                        {'text': AI_TEXT.split('. ')[1], 'why': 'Stacks vague workplace language and familiar AI-style phrases.'}],
                          'examples': []}},
    'humanize': {'id': 'live-humanize', 'skill': 'humanize', 'status': 'succeeded', 'cached': False, 'error': None, 'charged_usd': '0',
                 'output': {'version': 'humanize-v1.4', 'model': 'haiku', 'text': 'This transition works for teams of all sizes.\n',
                            'ai_before': 0.94565, 'ai_after': 0.01138, 'label_after': 'human', 'passes': True, 'invented_blocked': []}},
}

# Errors and answers missing their fields: each has its own copy, and none is a crash.
ODD = [
    ('free limit', 'detect', {'status': 429, 'body': {'error': {'type': 'free_limit', 'message': 'Free calls are used up.',
                                                                 'signup_url': 'javascript:alert(1)', 'retry_after_seconds': 600}},
                              'headers': {'X-Free-Remaining': '0'}}),
    ('504 too_long', 'humanize', {'status': 504, 'body': {'error': {'type': 'too_long',
                                                                    'message': 'The text took longer than 85 s to process; send a shorter text. Not charged.'}}}),
    ('HTML error page', 'detect', {'status': 502, 'html': '<!doctype html><title>502</title>Bad gateway'}),
    ('failed run', 'humanize', {'status': 200, 'body': dict(FIXTURES['humanize'], status='failed', output=None,
                                                            error='The model provider failed. You were not charged.')}),
]

# The rewrite headline says "No longer" or "Still" only when the detector model put the text sent in the AI class.
HEADLINES = [
    ({'ai_before': 0.95, 'ai_after': 0.01, 'passes': True}, 'No longer reads as AI'),
    ({'ai_before': 0.08, 'ai_after': 0.02, 'passes': True}, "Doesn't read as AI"),
    ({'ai_before': 0.88, 'ai_after': 0.71, 'passes': False, 'label_after': 'ai'}, 'Still reads as AI'),
    ({'ai_before': 0.3, 'ai_after': 0.62, 'passes': False, 'label_after': 'ai'}, 'Reads as AI'),
    ({'ai_before': None, 'ai_after': 0.04, 'passes': True}, "Doesn't read as AI"),
]


class MockApi:
    """Answers the skill calls with FIXTURES, or with what's queued: {status, body | html, headers} or {'drop': True}
    (cut the connection). `requests` records (method, path, headers, body) of each call."""

    def __init__(self):
        self.queue = []
        self.requests = []
        self.free = 9
        self.delay = 0
        mock = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                mock.handle(self, None)

            def do_POST(self):
                n = int(self.headers.get('Content-Length') or 0)
                mock.handle(self, json.loads(self.rfile.read(n).decode('utf-8')) if n else None)

        self.server = HTTPServer(('127.0.0.1', 0), Handler)
        self.url = 'http://127.0.0.1:%d' % self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()

    def handle(self, h, body):
        self.requests.append((h.command, h.path, dict(h.headers), body))
        if self.delay:
            threading.Event().wait(self.delay)
        if h.path == '/v1/me':
            if h.headers.get('Authorization') == 'Bearer p3n_good':
                return self.send(h, 200, FIXTURES['me'])
            return self.send(h, 401, {'error': {'type': 'unauthorized', 'message': 'Invalid API key.'}})
        nxt = self.queue.pop(0) if self.queue else None
        if nxt and nxt.get('drop'):
            h.close_connection = True
            h.connection.close()
            return
        if nxt and 'html' in nxt:
            return self.send(h, nxt['status'], nxt['html'], 'text/html', nxt.get('headers'))
        if nxt:
            return self.send(h, nxt.get('status', 200), nxt.get('body'), headers=nxt.get('headers'))
        skill = h.path.rsplit('/', 1)[-1].replace('mock-', '')
        if skill not in FIXTURES:
            return self.send(h, 404, {'error': {'type': 'not_found', 'message': 'No such skill.'}})
        return self.send(h, 200, copy.deepcopy(FIXTURES[skill]))

    def send(self, h, status, body, ctype='application/json', headers=None):
        raw = (body if isinstance(body, str) else json.dumps(body)).encode('utf-8')
        h.send_response(status)
        h.send_header('Content-Type', ctype)
        h.send_header('Content-Length', str(len(raw)))
        if 'Authorization' not in h.headers and h.path != '/v1/me':
            free = (headers or {}).get('X-Free-Remaining', str(self.free))
            h.send_header('X-Free-Remaining', free)
        for k, v in (headers or {}).items():
            if k != 'X-Free-Remaining':
                h.send_header(k, v)
        h.end_headers()
        h.wfile.write(raw)
