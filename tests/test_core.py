# The copy, the checks before sending, and the cards, in plain Python.
import copy
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib import api, core  # noqa: E402
from mock import AI_TEXT, FIXTURES, HEADLINES, SHORT  # noqa: E402


def run_of(skill, **output):
    run = copy.deepcopy(FIXTURES[skill])
    run['output'].update(output)
    return run


class CoreTest(unittest.TestCase):
    def test_headlines(self):
        for scores, want in HEADLINES:
            self.assertEqual(core.headline(scores), want, scores)

    def test_rounding_and_tone(self):
        self.assertEqual([core.pct(x) for x in (0.005, 0.015, 0.954, 0.955, 1, None)], [1, 2, 95, 96, 100, 0])
        self.assertEqual([core.tone(x) for x in (0.7, 0.69, 0.4, 0.39)], ['ai', 'mid', 'mid', 'human'])

    def test_too_long(self):
        e = core.input_error('detect', 'x' * 5001, False)
        self.assertEqual(e.type, 'too_long')
        self.assertEqual(e.message, "That's 5,001 characters. Without an API key p3n takes up to 5,000. Select less, or add a key.")
        self.assertIsNone(core.input_error('detect', 'x' * 5001, True))
        self.assertIn('takes up to 200,000', core.input_error('humanize', 'x' * 200001, True).message)

    def test_too_short(self):
        e = core.input_error('detect', 'x' * 39, False)
        self.assertEqual(e.message, 'Check needs at least 40 characters, a sentence or two. Select more.')
        self.assertIsNone(core.input_error('detect', 'x' * 40, False))
        e = core.input_error('humanize', 'Short one.', False)
        self.assertEqual((e.type, e.message), ('too_short', 'Rewrite needs at least a sentence of eight words. Select more.'))
        self.assertIsNone(core.input_error('humanize', 'One two three four five six seven eight.', False))
        self.assertIsNone(core.input_error('humanize', SHORT, False))
        # Numbers aren't words: seven here.
        self.assertEqual(core.input_error('humanize', 'In 2029 the team shipped 3 of its 12 apps.', True).type, 'too_short')
        self.assertEqual(core.input_error('humanize', '   ', False).type, 'too_short')

    def test_rewrite_words(self):
        # Counted as the API counts them: runs of the letters A to Z, ' and -.
        self.assertEqual(core.rewrite_words("It's a well-known fact, in 2029."), 5)
        self.assertEqual(core.rewrite_words('naïve café'), 3)  # na, ve, caf
        self.assertEqual(core.rewrite_words('Привет, мир'), 0)
        self.assertEqual(core.rewrite_words(None), 0)
        self.assertEqual(api.MIN_REWRITE_WORDS, 8)

    def test_bad_output(self):
        self.assertIsNone(core.bad_output(FIXTURES['detect']))
        self.assertIsNone(core.bad_output(FIXTURES['humanize']))
        for run in (dict(FIXTURES['detect'], output={'version': 'detect-v9'}),
                    dict(FIXTURES['humanize'], output={'version': 'humanize-v9', 'ai_after': 0.1}),
                    run_of('detect', ai=True),
                    run_of('humanize', ai_after='0.1')):
            e = core.bad_output(run)
            self.assertIn('without a result', e.message)

    def test_shape_rewrite(self):
        self.assertEqual(core.shape_rewrite('  Old text.\n', 'New text.\n'), '  New text.\n')
        self.assertEqual(core.shape_rewrite('Old text.', 'New text.\n'), 'New text.')
        self.assertEqual(core.shape_rewrite('Old\n\ntext.', '  New\n\ntext.  '), 'New\n\ntext.')

    def test_locate(self):
        text = 'Intro line.\n' + AI_TEXT
        s = FIXTURES['detect']['output']['sentences'][0]['text']
        a, b = core.locate(s, text)
        self.assertEqual(text[a:b], s)
        wrapped = AI_TEXT.replace('effective communication', 'effective\n  communication')
        a, b = core.locate(s, wrapped)
        self.assertTrue(wrapped[a:b].startswith("In today's") and wrapped[a:b].endswith('ever.'))
        self.assertIsNone(core.locate('Not in there.', text))
        self.assertIsNone(core.locate('', text))

    def test_hits_skip_listed_sentences(self):
        o = {'sentences': [{'text': 'One.  Two', 'why': 'x'}],
             'examples': [{'sentence': 'One. Two', 'rules': []}, {'sentence': 'Three.', 'rules': [{'name': 'Filler (LLM)'}]}]}
        self.assertEqual(core.hits(o), [(0, {'sentence': 'Three.', 'rules': [{'name': 'Filler (LLM)'}]})])
        html = core.detect_html({'skill': 'detect', 'output': dict(o, ai=0.5, label='mixed')})
        self.assertIn('Filler<', html)
        self.assertNotIn('(LLM)', html)

    def test_free_limit_copy(self):
        e = api.ApiError(429, 'free_limit', 'x', signup_url='javascript:alert(1)', retry_after=600, free=0)
        html = core.error_html('detect', e)
        self.assertIn('<p>The free calls for this hour are used up; more in about 10 min.</p>', html)
        self.assertIn('Sign up at p3n.si for free credit, then create an API key and add it to keep going.', html)
        self.assertIn('href="https://p3n.si/register"', html)
        self.assertNotIn('javascript', html)
        e.signup_url = 'https://p3n.si/register?ref=free'
        self.assertIn('href="https://p3n.si/register?ref=free"', core.error_html('detect', e))
        self.assertIn('href="p3n:key"', html)

    def test_other_errors(self):
        self.assertIn('href="https://p3n.si/dashboard/billing"', core.error_html('detect', api.ApiError(402, 'insufficient_credit', 'x')))
        self.assertIn('didn&#x27;t accept your API key', core.error_html('humanize', api.ApiError(401, 'unauthorized', 'x')))
        self.assertIn('Wait a minute', core.error_html('detect', api.ApiError(429, 'rate_limited', 'x')))
        self.assertIn('&lt;b&gt;', core.error_html('detect', api.ApiError(0, 'network', '<b>')))

    def test_error_summary(self):
        # The status bar's line is the card's first one, not the API's own message.
        for e, want in ((api.ApiError(429, 'free_limit', 'Free calls are used up.', retry_after=600),
                         'The free calls for this hour are used up; more in about 10 min.'),
                        (api.ApiError(429, 'free_limit', 'Free calls are used up.'), 'The free calls for this hour are used up.'),
                        (api.ApiError(402, 'insufficient_credit', 'x'), 'Your p3n balance is too low for this.'),
                        (api.ApiError(401, 'unauthorized', 'x'), "p3n didn't accept your API key. It may have been revoked."),
                        (api.ApiError(429, 'rate_limited', 'x'), 'Too many requests at once. Wait a minute and try again.'),
                        (api.ApiError(0, 'network', api.NETWORK), api.NETWORK),
                        (api.ApiError(500, 'error', ''), 'Something went wrong.')):
            self.assertEqual(core.error_summary(e), want)
            self.assertIn('<p>{}</p>'.format(core.esc(want)), core.error_html('detect', e))

    def test_free_line(self):
        self.assertEqual(core.free_line(None, False), 'Free without a key: 10 calls an hour, up to 5,000 characters each.')
        self.assertEqual(core.free_line(1, False), '1 free call left this hour without a key.')
        self.assertEqual(core.free_line(9, False), '9 free calls left this hour without a key.')
        self.assertEqual(core.free_line(9, True), '')

    def test_meta_line(self):
        run = dict(FIXTURES['detect'], cached=True, charged_usd='0.002600')
        self.assertEqual(core.meta_line(run, True), 'detect-v1.2 · from cache · $0.0026')
        self.assertEqual(core.meta_line(run, False), 'detect-v1.2 · from cache')

    def test_detect_card(self):
        run = run_of('detect', verdict='<script>alert(1)</script>')
        html = core.detect_html(run, False, 9, found={0})
        for want in ('Likely AI-written', '96%', 'likely AI', 'Sentences that read as AI', 'A stock opening with a vague claim.',
                     'href="p3n:goto:s0"', 'Confidence medium: Two short sentences.', 'A score is a probability, not proof.',
                     '9 free calls left this hour', '&lt;script&gt;', 'href="p3n:rewrite"', 'href="p3n:close"'):
            self.assertIn(want, html)
        self.assertNotIn('<script>', html)
        self.assertNotIn('p3n:goto', core.detect_html(run))  # not found in the buffer: no link

    def test_rewrite_card(self):
        run = FIXTURES['humanize']
        html = core.humanize_html(run, SHORT, 'This transition helps teams of all sizes.', state={})
        for want in ('No longer reads as AI', '95% → </span>1%', 'AI class', 'Detector model: Likely human-written',
                     'This transition helps teams of all sizes.', 'Replace selection', 'Copy', 'Try another rewrite',
                     'Kept 7 of 23 words.', 'The rewrite drops filler and general statements that carry no fact.',
                     'Check that every claim you need is still there.'):
            self.assertIn(want, html)
        self.assertNotIn('Humanize', html)
        self.assertNotIn('Replace selection', core.humanize_html(run, SHORT, 'x', can_replace=False))
        done = core.humanize_html(run, SHORT, 'x', state={'replaced': True, 'note': ('Replaced. Ctrl+Z undoes it.', 'ok')})
        self.assertIn('✓ Replaced', done)
        self.assertIn('note-ok">Replaced. Ctrl+Z undoes it.', done)
        blocked = core.humanize_html(run_of('humanize', invented_blocked=['2019', 'Acme']), SHORT, 'x')
        self.assertIn('Kept as sent where a rewrite added 2019, Acme, which the text does not have.', blocked)
        self.assertIn('line one<br>line two', core.humanize_html(run, 'a b', 'line one\nline two'))

    def test_busy_card(self):
        self.assertIn('Checking… 3 s', core.busy_html('detect', 3))
        self.assertIn('(a rewrite takes up to a minute)', core.busy_html('humanize', 9))
        self.assertNotIn('a rewrite takes', core.busy_html('humanize', 8))
        self.assertIn('href="p3n:cancel"', core.busy_html('detect', 0))
        # The status bar says the same as the card.
        self.assertEqual(core.busy_line('detect', 12), 'Checking… 12 s')
        self.assertEqual(core.busy_line('humanize', 8), 'Rewriting… 8 s')
        self.assertEqual(core.busy_line('humanize', 9), 'Rewriting… 9 s (a rewrite takes up to a minute)')

    def test_status_summary(self):
        self.assertEqual(core.status_summary(FIXTURES['detect']), '96% likely AI. Likely AI-written')
        self.assertEqual(core.status_summary(FIXTURES['humanize']), 'No longer reads as AI, 1% AI class')


if __name__ == '__main__':
    unittest.main()
