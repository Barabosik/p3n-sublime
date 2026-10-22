# p3n for Sublime Text: check selected text for AI writing, and rewrite it. Results show in a card under the
# selection (a phantom, coloured by the colour scheme). Text is sent only when you run a command, from a thread of
# the package's own, so the editor never waits on the network.
import os
import threading
import time
import traceback

import sublime
import sublime_plugin

from .lib import api, core

SETTINGS = 'p3n.sublime-settings'
SOURCE = 'p3n.source'  # what was sent, tracked as the buffer changes
MARK = 'p3n.mark.'  # + s0, s1… (sentences) and h0, h1… (pattern hits)

_runs = {}  # view id -> the view's current Run
_phantoms = {}  # view id -> PhantomSet
_free = None  # X-Free-Remaining from p3n's last keyless answer (it counts per IP, not per view)
_seq = 0


def settings():
    return sublime.load_settings(SETTINGS)


def api_key():
    return (settings().get('api_key') or '').strip() or os.environ.get('P3N_API_KEY', '').strip()


def undo_keys():
    return 'Cmd+Z' if sublime.platform() == 'osx' else 'Ctrl+Z'


class Run:
    def __init__(self, view, skill, sent, options):
        global _seq
        _seq += 1
        self.id = _seq
        self.view = view
        self.skill = skill
        self.sent = sent
        self.options = options
        self.has_key = False
        # The text of each tracked region when the run began: Replace checks the buffer still has it.
        self.expected = [view.substr(r) for r in view.get_regions(SOURCE)]
        self.cancel = threading.Event()
        self.started = time.time()
        self.busy = True
        self.result = None
        self.error = None
        self.marks = []  # region keys added for this run's sentences and hits
        self.found = set()
        self.found_hits = set()
        self.state = {}

    @property
    def rewrite(self):
        source = self.expected[0] if len(self.expected) == 1 else self.sent
        return core.shape_rewrite(source, self.result['output']['text'])


def selected_regions(view):
    return [r for r in view.sel() if not r.empty()]


def has_selection(view):
    return any(not r.empty() for r in view.sel())


def anchor(view):
    """Where the card goes: under the line where the text sent ends."""
    regions = view.get_regions(SOURCE)
    if not regions:
        return sublime.Region(view.sel()[0].b if len(view.sel()) else 0)
    end = regions[-1].end()
    if regions[-1].size() and end > 0 and view.substr(end - 1) == '\n':
        end -= 1
    return sublime.Region(end)


def show_card(view, content):
    vid = view.id()
    if vid not in _phantoms:
        _phantoms[vid] = sublime.PhantomSet(view, 'p3n')
    _phantoms[vid].update([sublime.Phantom(anchor(view), content, sublime.LAYOUT_BLOCK,
                                           lambda href, view=view: navigate(view, href))])


def hint(view, msg):
    """A message that needs no card: a popup at the caret, and the status bar."""
    sublime.status_message('p3n: ' + msg)
    if len(view.sel()):
        view.show_popup(core.hint_html(msg), sublime.HIDE_ON_MOUSE_MOVE_AWAY, view.sel()[0].b, 640, 320)


def clear_marks(view, run=None):
    for key in (run.marks if run else []):
        view.erase_regions(key)


def close(view):
    run = _runs.pop(view.id(), None)
    if run:
        run.cancel.set()
        clear_marks(view, run)
    view.erase_regions(SOURCE)
    view.erase_status('p3n')
    ps = _phantoms.pop(view.id(), None)
    if ps:
        ps.update([])


def render(run):
    view = run.view
    if _runs.get(view.id()) is not run or not view.is_valid():
        return
    if run.busy:
        content = core.busy_html(run.skill, int(time.time() - run.started))
    elif run.error:
        content = core.error_html(run.skill, run.error)
    elif run.skill == 'detect':
        content = core.detect_html(run.result, run.has_key, _free, run.found, run.found_hits)
    else:
        content = core.humanize_html(run.result, run.sent, run.rewrite, run.has_key, _free,
                                     can_replace=len(run.expected) == 1 and not view.is_read_only(), state=run.state)
    show_card(view, content)


def start(view, skill, regions=None, sent=None, options=None):
    """Sends the text in `regions` (the selection by default) to p3n, and shows a card under it."""
    regions = regions if regions is not None else selected_regions(view)
    if not regions:
        return hint(view, 'Nothing is selected. Select some text first.')
    if skill == 'humanize' and len(regions) > 1:
        return hint(view, 'p3n rewrites one selection at a time. Select one block of text.')
    old = _runs.get(view.id())
    if old:
        old.cancel.set()
        clear_marks(view, old)
        view.erase_status('p3n')  # its "Checking… N s", if it was still going
    view.add_regions(SOURCE, regions, '', '', sublime.HIDDEN)
    if sent is None:
        sent = '\n\n'.join(view.substr(r) for r in regions).strip()
    run = Run(view, skill, sent, options)
    run.has_key = bool(api_key())
    _runs[view.id()] = run
    err = core.input_error(skill, sent, run.has_key)
    if err:
        run.busy = False
        run.error = err
        sublime.status_message('p3n: ' + core.error_summary(err))
        return render(run)
    render(run)
    tick(run)
    key = api_key()
    threading.Thread(target=work, args=(run, key), name='p3n-' + skill, daemon=True).start()


def work(run, key):
    result = error = free = None
    try:
        result, free = api.run_skill(run.skill, run.sent, key=key, options=run.options, cancel=run.cancel)
    except api.Cancelled:
        return
    except api.ApiError as e:
        error, free = e, e.free
    except Exception as e:  # a bug here must still end the run, not leave it spinning
        traceback.print_exc()
        error = api.ApiError(0, 'error', 'Something went wrong: {}'.format(e))
    sublime.set_timeout(lambda: finish(run, result, error, free), 0)


def finish(run, result, error, free):
    global _free
    if free is not None:
        _free = free
    view = run.view
    if run.cancel.is_set() or _runs.get(view.id()) is not run or not view.is_valid():
        return
    run.busy = False
    view.erase_status('p3n')
    if error is None:
        error = core.bad_output(result)
    if error:
        run.error = error
        sublime.status_message('p3n: ' + core.error_summary(error))
        return render(run)
    run.result = result
    if run.skill == 'detect':
        mark(run)
    sublime.status_message('p3n: ' + core.status_summary(result))
    render(run)


def mark(run):
    """Underlines the sentences that read as AI (and the pattern hits) where they are in the text sent."""
    view = run.view
    o = run.result['output']
    found = {}
    for i, s in enumerate((o.get('sentences') or [])[:core.MAX_SENTENCES]):
        r = find(view, s.get('text'))
        if r:
            found['s%d' % i] = (r, s.get('why'))
    for i, e in core.hits(o):
        r = find(view, e.get('sentence'))
        if r:
            found['h%d' % i] = (r, None)
    run.found = set(int(k[1:]) for k in found if k[0] == 's')
    run.found_hits = set(int(k[1:]) for k in found if k[0] == 'h')
    if not settings().get('highlight_sentences', True):
        # Kept, hidden, so the card's quotes still jump to them.
        for k, (r, _) in found.items():
            view.add_regions(MARK + k, [r], '', '', sublime.HIDDEN)
            run.marks.append(MARK + k)
        return
    reasons = settings().get('show_reasons', False)
    red = view.style_for_scope('region.redish')
    color = red.get('foreground') or red.get('background') or ''
    for k, (r, why) in found.items():
        sentence = k[0] == 's'
        flags = sublime.DRAW_NO_FILL | sublime.DRAW_NO_OUTLINE | (sublime.DRAW_SOLID_UNDERLINE if sentence else sublime.DRAW_STIPPLED_UNDERLINE)
        extra = {}
        if sentence and reasons and why:
            extra = {'annotations': [core.annotation_html(why)], 'annotation_color': color,
                     'on_navigate': lambda href, view=view: navigate(view, href)}
        view.add_regions(MARK + k, [r], 'region.redish' if sentence else 'region.orangish', '', flags, **extra)
        run.marks.append(MARK + k)


def find(view, sentence):
    for r in view.get_regions(SOURCE):
        at = core.locate(sentence, view.substr(r))
        if at:
            return sublime.Region(r.begin() + at[0], r.begin() + at[1])
    return None


def tick(run):
    if not run.busy or _runs.get(run.view.id()) is not run:
        return
    what = 'Checking' if run.skill == 'detect' else 'Rewriting'
    run.view.set_status('p3n', 'p3n: {}… {} s'.format(what, int(time.time() - run.started)))
    render(run)
    sublime.set_timeout(lambda: tick(run), 1000)


def navigate(view, href):
    """Every link in a card lands here."""
    run = _runs.get(view.id())
    if href.startswith('https://') or href.startswith('http://'):
        # Only p3n's own pages; anything else in an answer isn't followed.
        if href.startswith(api.SITE + '/'):
            sublime.run_command('open_url', {'url': href})
        return
    if href == 'p3n:close':
        return close(view)
    if href == 'p3n:key':
        return view.window() and view.window().run_command('p3n_set_api_key')
    if not run:
        return
    if href == 'p3n:cancel':
        return view.run_command('p3n_cancel')
    if href.startswith('p3n:goto:'):
        regions = view.get_regions(MARK + href[len('p3n:goto:'):])
        if regions:
            view.sel().clear()
            view.sel().add(regions[0])
            view.show_at_center(regions[0])
        return
    if href == 'p3n:rewrite':
        regions = view.get_regions(SOURCE)
        return start(view, 'humanize', regions) if regions else None
    if run.skill != 'humanize' or not run.result:
        return
    if href == 'p3n:replace':
        return view.run_command('p3n_replace', {'run': run.id})
    if href == 'p3n:copy':
        sublime.set_clipboard(run.rewrite.strip())
        run.state['copied'] = True
        render(run)

        def uncopy():
            run.state['copied'] = False
            render(run)
        return sublime.set_timeout(uncopy, 1500)
    if href == 'p3n:again':
        return start(view, 'humanize', view.get_regions(SOURCE), sent=run.sent, options={'fresh': True})


class P3nCheckCommand(sublime_plugin.TextCommand):
    """Check for AI. The right-click menu passes menu=true, so it shows only with text selected."""

    def run(self, edit, menu=False):
        start(self.view, 'detect')

    def is_visible(self, menu=False):
        return not menu or has_selection(self.view)


class P3nRewriteCommand(sublime_plugin.TextCommand):
    def run(self, edit, menu=False):
        start(self.view, 'humanize')

    def is_visible(self, menu=False):
        return not menu or has_selection(self.view)


class P3nReplaceCommand(sublime_plugin.TextCommand):
    """Puts the rewrite where the text sent was: one edit, so one undo."""

    def run(self, edit, run=None):
        view = self.view
        r = _runs.get(view.id())
        if not r or r.id != run or not r.result:
            return
        regions = view.get_regions(SOURCE)
        if r.state.get('replaced'):
            why = 'p3n already replaced that selection.'
        elif view.is_read_only():
            why = 'This file is read-only.'
        elif len(regions) != 1 or len(r.expected) != 1 or view.substr(regions[0]) != r.expected[0]:
            why = 'The text changed since you selected it.'
        else:
            why = None
        if why:
            r.state['note'] = ('{} Copy the rewrite and paste it instead.'.format(why), 'warn')
            return render(r)
        text = r.rewrite
        at = regions[0].begin()
        view.replace(edit, regions[0], text)
        new = sublime.Region(at, at + len(text))
        view.add_regions(SOURCE, [new], '', '', sublime.HIDDEN)
        lead = len(text) - len(text.lstrip())
        trail = len(text) - len(text.rstrip())
        view.sel().clear()
        view.sel().add(sublime.Region(at + lead, at + len(text) - trail))
        # A later "Try another rewrite" may replace this rewrite in turn.
        r.expected = [text]
        r.state['replaced'] = True
        r.state['note'] = ('Replaced. {} undoes it.'.format(undo_keys()), 'ok')
        sublime.status_message('p3n: replaced. {} undoes it.'.format(undo_keys()))
        render(r)

    def is_visible(self, run=None):
        return False


class P3nCancelCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        r = _runs.get(self.view.id())
        if r and r.busy:
            close(self.view)
            sublime.status_message('p3n: cancelled.')

    def is_visible(self):
        r = _runs.get(self.view.id())
        return bool(r and r.busy)


class P3nClearCommand(sublime_plugin.TextCommand):
    """Closes the card and the underlines."""

    def run(self, edit):
        close(self.view)

    def is_enabled(self):
        return self.view.id() in _runs


class P3nSetApiKeyCommand(sublime_plugin.WindowCommand):
    def run(self):
        self.window.show_input_panel('p3n API key (no key yet? sign up at p3n.si, then create one at p3n.si/dashboard/keys):',
                                     '', self.check, None, None)

    def check(self, key):
        # Checks the key with p3n and saves it only if it works, so a typo never replaces a good key.
        key = key.strip()
        if not key:
            return
        if not key.startswith('p3n_') or len(key.split()) != 1:
            return self.say('Keys start with p3n_. Copy the whole key from the API keys page.')
        sublime.status_message('p3n: checking the key…')

        def work():
            try:
                m = api.me(key)
            except api.ApiError as e:
                msg = "p3n doesn't accept this key, so it wasn't saved. Copy it again from the API keys page." \
                    if e.type == 'unauthorized' else e.message + ' The key wasn\'t saved.'
                return sublime.set_timeout(lambda: self.say(msg), 0)
            sublime.set_timeout(lambda: self.save(key, m or {}), 0)
        threading.Thread(target=work, name='p3n-key', daemon=True).start()

    def save(self, key, m):
        settings().set('api_key', key)
        sublime.save_settings(SETTINGS)
        try:
            balance = '${:.2f}'.format(float(m.get('balance_usd') or 0))
        except (TypeError, ValueError):
            balance = '?'
        self.say('Saved. Signed in as {}, balance {}.'.format(m.get('email') or 'your account', balance))

    def say(self, msg):
        view = self.window.active_view()
        if view:
            hint(view, msg)
        else:
            sublime.status_message('p3n: ' + msg)


class P3nRemoveApiKeyCommand(sublime_plugin.WindowCommand):
    def run(self):
        settings().erase('api_key')
        sublime.save_settings(SETTINGS)
        msg = 'Key removed. p3n still works without one, with the free hourly limit.'
        if os.environ.get('P3N_API_KEY', '').strip():
            msg = 'Key removed from the settings. P3N_API_KEY is still set, so p3n keeps using that key.'
        view = self.window.active_view()
        if view:
            hint(view, msg)

    def is_visible(self):
        return bool((settings().get('api_key') or '').strip())


class P3nListener(sublime_plugin.EventListener):
    def on_close(self, view):
        run = _runs.pop(view.id(), None)
        if run:
            run.cancel.set()
        _phantoms.pop(view.id(), None)


def plugin_unloaded():
    # Disabled, removed or upgraded: leave no card or underline behind.
    for run in list(_runs.values()):
        close(run.view)
