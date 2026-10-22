# What p3n shows: the result cards (minihtml, coloured by the user's colour scheme), the copy, and the checks
# made before anything is sent. No sublime imports, so the tests run it in plain Python.
import html
import math
import re

from .api import ApiError, FREE_PER_HOUR, LINKS, MAX_CHARS, MIN_DETECT_CHARS, SITE

LABELS = {'human': 'Likely human-written', 'ai': 'Likely AI-written', 'ai_edited': 'AI-written, then edited',
          'humanized': 'AI-written, then humanized', 'mixed': 'Mixed: part human, part AI'}
ACTIONS = {'detect': 'Check for AI', 'humanize': 'Rewrite'}
MAX_SENTENCES = 5
MAX_HITS = 6


def pct(x):
    return int(math.floor((x or 0) * 100 + 0.5))


def tone(x):
    return 'ai' if x >= 0.7 else 'mid' if x >= 0.4 else 'human'


def words(t):
    return len((t or '').split())


def clip(t, n=220):
    return t[:n - 1] + '…' if t and len(t) > n else t


def plural(n, one, many):
    return one if n == 1 else many


def thousands(n):
    return '{:,}'.format(n)


def limit(has_key):
    return MAX_CHARS['key'] if has_key else MAX_CHARS['free']


def input_error(skill, text, has_key):
    """The reason not to send `text` (already trimmed), as an ApiError, or None."""
    if len(text) > limit(has_key):
        if has_key:
            msg = "That's {} characters; the API takes up to {}. Select less.".format(thousands(len(text)), thousands(MAX_CHARS['key']))
        else:
            msg = "That's {} characters. Without an API key p3n takes up to {}. Select less, or add a key.".format(
                thousands(len(text)), thousands(MAX_CHARS['free']))
        return ApiError(422, 'too_long', msg)
    if skill == 'detect' and len(text) < MIN_DETECT_CHARS:
        return ApiError(422, 'too_short', 'Check needs at least {} characters, a sentence or two. Select more.'.format(MIN_DETECT_CHARS))
    return None


def bad_output(run):
    """Without the fields this version reads, say so: don't show 0% or a script error."""
    o = run.get('output') if isinstance(run, dict) else None
    o = o if isinstance(o, dict) else {}
    number = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool)
    if run.get('skill') == 'detect' and not number(o.get('ai')) \
            or run.get('skill') == 'humanize' and (not isinstance(o.get('text'), str) or not number(o.get('ai_after'))):
        return ApiError(502, 'bad_output', 'p3n answered without a result. Try again. If it keeps happening, update the package.')
    return None


def headline(o):
    # "No longer" and "Still" only when the detector model put the text sent in the AI class: it can score text at
    # 0% that Check called likely AI, and "No longer reads as AI, 0% -> 2%" reads wrong.
    was_ai = o.get('ai_before') is not None and o['ai_before'] >= 0.5
    if o.get('passes'):
        return 'No longer reads as AI' if was_ai else "Doesn't read as AI"
    return 'Still reads as AI' if was_ai else 'Reads as AI'


def shape_rewrite(source, rewrite):
    # The rewrite ends with a line break; keep only the whitespace the selection itself had at each end, so Replace
    # doesn't join it to the line or word before or after.
    lead = re.match(r'\s*', source).group(0)
    trail = re.search(r'\s*$', source).group(0) if source.strip() else ''
    return lead + rewrite.strip() + trail


def norm(t):
    return ' '.join((t or '').split())


def locate(sentence, text):
    """Where `sentence` is in `text`, as (start, end), or None. The API may collapse whitespace and line breaks."""
    sentence = (sentence or '').strip()
    if not sentence:
        return None
    i = text.find(sentence)
    if i >= 0:
        return i, i + len(sentence)
    m = re.search(r'\s+'.join(re.escape(w) for w in sentence.split()), text)
    return (m.start(), m.end()) if m else None


def retry_minutes(e):
    return max(1, int(math.ceil(e.retry_after / 60.0))) if e.retry_after else None


def signup_link(e):
    # The link comes from the API's answer: follow it only to p3n's own site.
    url = e.signup_url or ''
    return url if url.startswith(SITE + '/') else LINKS['signup']


def free_line(free, has_key):
    if has_key:
        return ''
    if free is None:
        return 'Free without a key: {} calls an hour, up to {} characters each.'.format(FREE_PER_HOUR, thousands(MAX_CHARS['free']))
    return '{} free {} left this hour without a key.'.format(free, plural(free, 'call', 'calls'))


def meta_line(run, has_key):
    o = run.get('output') or {}
    bits = [str(o.get('version') or run.get('skill') or '')]
    if run.get('cached'):
        bits.append('from cache')
    try:
        charged = float(run.get('charged_usd') or 0)
    except (TypeError, ValueError):
        charged = 0
    if has_key and charged:
        bits.append('${:.4f}'.format(charged))
    return ' · '.join(b for b in bits if b)


def status_summary(run):
    """One line for the status bar."""
    o = run['output']
    if run.get('skill') == 'detect':
        return '{}% likely AI. {}'.format(pct(o['ai']), LABELS.get(o.get('label'), 'Unknown'))
    return '{}, {}% AI class'.format(headline(o), pct(o['ai_after']))


# ---- minihtml ------------------------------------------------------------------------------

# minihtml has no tables, flexbox or widths in %: blocks, inline-blocks, padding and the scheme's colours.
CSS = '''
body { margin: 0; padding: 0; font-family: system; }
.card { margin: 0.4rem 0 0.6rem 0; padding: 0.55rem 0.8rem 0.6rem 0.8rem; border-radius: 0.3rem;
  background-color: color(var(--background) blend(var(--foreground) 92%)); border-left: 0.2rem solid var(--bluish); }
.card.error { border-left-color: var(--redish); }
.top { color: color(var(--foreground) a(0.6)); font-size: 0.9rem; margin-bottom: 0.3rem; }
.brand { font-weight: bold; color: var(--foreground); }
.brand span { color: var(--bluish); }
.label { font-weight: bold; font-size: 1.1rem; }
.pct { font-weight: bold; font-size: 1.4rem; }
.from { font-weight: normal; font-size: 1rem; color: color(var(--foreground) a(0.6)); }
.t-ai { color: var(--redish); }
.t-mid { color: var(--orangish); }
.t-human { color: var(--greenish); }
.meter { margin: 0.3rem 0 0.5rem 0; font-size: 0.3rem; line-height: 0.4rem; }
.meter span { border-radius: 0.2rem; }
.b-ai { background-color: var(--redish); }
.b-mid { background-color: var(--orangish); }
.b-human { background-color: var(--greenish); }
.b-rest { background-color: color(var(--foreground) a(0.12)); }
.small { font-size: 0.9rem; }
.muted { color: color(var(--foreground) a(0.6)); }
p { margin: 0.3rem 0 0.3rem 0; }
h3 { font-size: 0.95rem; margin: 0.6rem 0 0.2rem 0; }
ul { margin: 0.1rem 0 0.2rem 0; }
li { margin: 0.15rem 0 0.25rem 0; }
.why { color: color(var(--foreground) a(0.7)); font-size: 0.9rem; }
.quote { color: var(--foreground); }
a.quote { text-decoration: none; }
.rewrite { margin: 0.4rem 0 0.5rem 0; padding: 0.4rem 0.6rem; border-radius: 0.2rem;
  background-color: var(--background); border-left: 0.15rem solid var(--greenish); }
.row { margin: 0.5rem 0 0.2rem 0; }
.btn { text-decoration: none; padding: 0.15rem 0.6rem; border-radius: 0.25rem; color: var(--foreground);
  border: 1px solid color(var(--foreground) a(0.3)); }
.btn.primary { background-color: var(--bluish); color: var(--background); border-color: var(--bluish); }
.btn.done { background-color: var(--greenish); color: var(--background); border-color: var(--greenish); }
.link { text-decoration: none; color: var(--bluish); }
.note-ok { color: var(--greenish); }
.note-warn { color: var(--orangish); }
.note-error { color: var(--redish); }
'''

METER_REM = 16


def esc(t):
    return html.escape(str(t if t is not None else ''), quote=True)


def text_html(t):
    return esc(t).replace('\n', '<br>')


def button(label, href, kind=''):
    return '<a class="btn{}" href="{}">{}</a>'.format(' ' + kind if kind else '', esc(href), esc(label))


def page(body, kind=''):
    return '<body id="p3n"><style>{}</style><div class="card{}">{}</div></body>'.format(CSS, ' ' + kind if kind else '', body)


def top(action=None):
    brand = '<span class="brand">p<span>3</span>n</span>'
    return '<div class="top">{}{}</div>'.format(brand, ' · ' + esc(action) if action else '')


def meter(x, t):
    filled = max(0.3, METER_REM * min(1, max(0, x or 0)))
    return ('<div class="meter"><span class="b-{}" style="padding-left: {:.2f}rem">&nbsp;</span>'
            '<span class="b-rest" style="padding-left: {:.2f}rem">&nbsp;</span></div>').format(t, filled, max(0, METER_REM - filled))


def footer(run, has_key, free):
    bits = [b for b in (meta_line(run, has_key), free_line(free, has_key)) if b]
    return '<p class="small muted">{}</p>'.format(esc(' · '.join(bits)))


def busy_html(skill, seconds):
    what = 'Checking' if skill == 'detect' else 'Rewriting'
    extra = ' (a rewrite takes up to a minute)' if skill == 'humanize' and seconds > 8 else ''
    return page(top(ACTIONS[skill]) + '<p>{}… {} s{} &nbsp; {}</p>'.format(what, seconds, esc(extra), button('Cancel', 'p3n:cancel')))


def detect_html(run, has_key=False, free=None, found=(), found_hits=()):
    """`found` and `found_hits` are the indexes of the sentences and pattern hits located in the buffer: those
    become links that jump to them."""
    o = run['output']
    t = tone(o['ai'])
    parts = [top(ACTIONS['detect']),
             '<div><span class="label">{}</span> &nbsp; <span class="pct t-{}">{}%</span> <span class="muted">likely AI</span></div>'.format(
                 esc(LABELS.get(o.get('label'), 'Unknown')), t, pct(o['ai'])),
             meter(o['ai'], t)]
    if o.get('verdict'):
        parts.append('<p>{}</p>'.format(esc(o['verdict'])))
    conf = o.get('confidence') or {}
    if conf.get('level'):
        parts.append('<p class="small muted">Confidence {}: {}</p>'.format(esc(conf['level']), esc(conf.get('reason') or '')))
    sentences = (o.get('sentences') or [])[:MAX_SENTENCES]
    if sentences:
        items = []
        for i, s in enumerate(sentences):
            quote = '“{}”'.format(esc(clip(s.get('text'))))
            quote = '<a class="quote" href="p3n:goto:s{}">{}</a>'.format(i, quote) if i in found else '<span class="quote">{}</span>'.format(quote)
            why = '<span class="why">{}</span><br>'.format(esc(s['why'])) if s.get('why') else ''
            items.append('<li>{}{}</li>'.format(why, quote))
        parts.append('<h3>Sentences that read as AI</h3><ul>{}</ul>'.format(''.join(items)))
    hit_items = []
    for i, e in hits(o):
        rules = ' · '.join(re.sub(r'\s*\(LLM\)$', '', str(r.get('name') or '')) for r in (e.get('rules') or []))
        quote = '“{}”'.format(esc(clip(e.get('sentence'))))
        quote = '<a class="quote" href="p3n:goto:h{}">{}</a>'.format(i, quote) if i in found_hits else '<span class="quote">{}</span>'.format(quote)
        hit_items.append('<li><span class="why">{}</span><br>{}</li>'.format(esc(rules), quote))
    if hit_items:
        parts.append('<h3>Pattern hits</h3><ul>{}</ul>'.format(''.join(hit_items)))
    parts.append('<p class="small muted">A score is a probability, not proof. Read the sentences yourself before deciding anything about a person.</p>')
    parts.append('<div class="row">{} {}</div>'.format(button('Rewrite', 'p3n:rewrite'), button('Close', 'p3n:close')))
    parts.append(footer(run, has_key, free))
    return page(''.join(parts))


def hits(o):
    """Pattern hits worth listing: the ones whose sentence isn't already in the sentence list. Returns
    [(index, example)] with the index into this list."""
    shown = set(norm(s.get('text')) for s in (o.get('sentences') or [])[:MAX_SENTENCES])
    out = [e for e in (o.get('examples') or []) if norm(e.get('sentence')) not in shown][:MAX_HITS]
    return list(enumerate(out))


def humanize_html(run, sent, rewrite, has_key=False, free=None, can_replace=True, state=None):
    """`rewrite` is the text Replace puts back (shape_rewrite); `state` holds replaced, copied and a status note
    (text, kind)."""
    state = state or {}
    o = run['output']
    t = tone(o['ai_after'])
    before = pct(o['ai_before']) if o.get('ai_before') is not None else None
    frm = '<span class="from">{}% → </span>'.format(before) if before is not None else ''
    parts = [top(ACTIONS['humanize']),
             '<div><span class="label">{}</span> &nbsp; <span class="pct t-{}">{}{}%</span> <span class="muted">AI class</span></div>'.format(
                 esc(headline(o)), t, frm, pct(o['ai_after'])),
             '<p class="small muted">Detector model: {}</p>'.format(esc(LABELS.get(o.get('label_after'), 'unknown'))),
             meter(o['ai_after'], t),
             '<div class="rewrite">{}</div>'.format(text_html(rewrite.strip()))]
    buttons = []
    if can_replace:
        if state.get('replaced'):
            buttons.append(button('✓ Replaced', 'p3n:noop', 'done'))
        else:
            buttons.append(button('Replace selection', 'p3n:replace', 'primary'))
    buttons.append(button('✓ Copied' if state.get('copied') else 'Copy', 'p3n:copy', '' if can_replace else 'primary'))
    buttons.append(button('Try another rewrite', 'p3n:again'))
    buttons.append(button('Close', 'p3n:close'))
    parts.append('<div class="row">{}</div>'.format(' '.join(buttons)))
    if state.get('note'):
        msg, kind = state['note']
        parts.append('<p class="small note-{}">{}</p>'.format(kind, esc(msg)))
    if words(o['text']) / float(max(1, words(sent))) < 0.6:
        parts.append(('<p class="small note-warn">Kept {} of {} words. Most of this text was general statements with no facts in them, '
                      'and those are what reads as AI. Add the specifics you meant (who, what, numbers) and run it again.</p>').format(
                          words(o['text']), words(sent)))
    if o.get('invented_blocked'):
        parts.append('<p class="small muted">Kept as sent where a rewrite added {}, which the text does not have.</p>'.format(
            esc(', '.join(str(x) for x in o['invented_blocked']))))
    parts.append('<p class="small muted">The rewrite drops filler and general statements that carry no fact. Check that every claim you need is still there.</p>')
    parts.append(footer(run, has_key, free))
    return page(''.join(parts))


def error_summary(e):
    """The error's first line, in plain text: the card opens with it, and the status bar shows it."""
    if e.type == 'free_limit':
        mins = retry_minutes(e)
        return 'The free calls for this hour are used up{}.'.format('; more in about {} min'.format(mins) if mins else '')
    if e.type == 'insufficient_credit':
        return 'Your p3n balance is too low for this.'
    if e.type == 'unauthorized':
        return "p3n didn't accept your API key. It may have been revoked."
    if e.type == 'rate_limited':
        return 'Too many requests at once. Wait a minute and try again.'
    return e.message or 'Something went wrong.'


def error_parts(e):
    """The error's copy and buttons: ([paragraph html], [button html])."""
    first = '<p>{}</p>'.format(esc(error_summary(e)))
    if e.type == 'free_limit':
        return ([first, '<p class="small">Sign up at p3n.si for free credit, then create an API key and add it to keep going.</p>'],
                [button('Sign up', signup_link(e), 'primary'), button('Add an API key', 'p3n:key')])
    if e.type == 'insufficient_credit':
        return [first], [button('Top up', LINKS['billing'], 'primary')]
    if e.type == 'unauthorized':
        return [first], [button('Change the key', 'p3n:key')]
    return [first], []


def error_html(skill, e):
    paras, buttons = error_parts(e)
    buttons = buttons + [button('Close', 'p3n:close')]
    return page(top(ACTIONS.get(skill)) + ''.join(paras) + '<div class="row">{}</div>'.format(' '.join(buttons)), 'error')


def hint_html(msg):
    return page(top() + '<p>{}</p>'.format(esc(msg)))


def annotation_html(why):
    return '<body id="p3n-why"><span>{}</span></body>'.format(esc(clip(why, 90)))
