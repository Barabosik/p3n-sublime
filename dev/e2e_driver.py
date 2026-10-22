# Runs inside a portable Sublime Text (dev/e2e.py copies it to Data/Packages/User/p3n_e2e.py): selects text, runs
# p3n's commands, clicks the card's links (through the same callback a click uses), checks the buffer, and captures
# the window. With MOCK the API is tests/mock.py on 127.0.0.1; LIVE makes exactly two real keyless calls.
import ctypes
import importlib
import json
import os
import struct
import sys
import threading
import time
import traceback

import sublime

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = json.load(open(os.path.join(HERE, 'p3n_e2e.json'), encoding='utf-8'))
OUT = CONFIG['out']
results = []
log_lines = []


def log(msg):
    log_lines.append('%.1f %s' % (time.time() % 1000, msg))
    with open(os.path.join(OUT, 'log.txt'), 'w', encoding='utf-8') as f:
        f.write('\n'.join(log_lines) + '\n')


def check(name, ok, detail=''):
    results.append({'name': name, 'ok': bool(ok), 'detail': str(detail)[:1200]})
    log(('PASS ' if ok else 'FAIL ') + name + ('' if ok else ': ' + str(detail)[:1200]))


def save():
    with open(os.path.join(OUT, 'results.json'), 'w', encoding='utf-8') as f:
        json.dump({'results': results, 'done': True}, f, indent=1)


# ---- window capture (PrintWindow, so other windows never get into the picture) ------------------------------

def capture(window, name):
    # PrintWindow waits on Sublime's UI thread, which waits on this callback: capture from a thread, and let the
    # runner wait for the file.
    path = os.path.join(OUT, name + '.bmp')
    hwnd = window.hwnd()
    threading.Thread(target=_capture, args=(hwnd, name), daemon=True).start()
    return (lambda: os.path.exists(path), 10)


def _capture(hwnd, name):
    try:
        user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
        user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))

        class RECT(ctypes.Structure):
            _fields_ = [('l', ctypes.c_long), ('t', ctypes.c_long), ('r', ctypes.c_long), ('b', ctypes.c_long)]
        rc = RECT()
        user32.GetWindowRect(ctypes.c_void_p(hwnd), ctypes.byref(rc))
        w, h = rc.r - rc.l, rc.b - rc.t
        hdc = user32.GetWindowDC(ctypes.c_void_p(hwnd))
        mdc = gdi32.CreateCompatibleDC(ctypes.c_void_p(hdc))
        bmp = gdi32.CreateCompatibleBitmap(ctypes.c_void_p(hdc), w, h)
        gdi32.SelectObject(ctypes.c_void_p(mdc), ctypes.c_void_p(bmp))
        user32.PrintWindow(ctypes.c_void_p(hwnd), ctypes.c_void_p(mdc), 2)  # PW_RENDERFULLCONTENT
        header = struct.pack('<IiiHHIIiiII', 40, w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
        buf = ctypes.create_string_buffer(w * h * 4)
        gdi32.GetDIBits(ctypes.c_void_p(mdc), ctypes.c_void_p(bmp), 0, h, buf, ctypes.c_char_p(header), 0)
        with open(os.path.join(OUT, name + '.tmp'), 'wb') as f:
            f.write(b'BM' + struct.pack('<IHHI', 14 + 40 + w * h * 4, 0, 0, 54) + header + buf.raw)
        gdi32.DeleteObject(ctypes.c_void_p(bmp))
        gdi32.DeleteDC(ctypes.c_void_p(mdc))
        user32.ReleaseDC(ctypes.c_void_p(hwnd), ctypes.c_void_p(hdc))
        os.replace(os.path.join(OUT, name + '.tmp'), os.path.join(OUT, name + '.bmp'))
        log('shot ' + name)
    except Exception as e:
        log('shot failed %s: %s' % (name, e))


def place(window, w=1280, h=860):
    # Size and place the window without activating it, so the user's focus stays where it is.
    try:
        ctypes.windll.user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
        # ASYNCWINDOWPOS: posted, not sent, so it doesn't wait on the UI thread that's waiting on us.
        ctypes.windll.user32.SetWindowPos(ctypes.c_void_p(window.hwnd()), None, 60, 40, w, h, 0x4000 | 0x0010 | 0x0004)
    except Exception as e:
        log('place failed: %s' % e)


# ---- the step runner: a generator yields seconds to wait, or a condition (with an optional timeout) -----------

def run(gen):
    def step():
        try:
            what = next(gen)
        except StopIteration:
            return finish()
        except Exception:
            check('driver', False, traceback.format_exc()[-1200:])
            return finish()
        if isinstance(what, (int, float)):
            return sublime.set_timeout(step, int(what * 1000))
        cond, timeout = what if isinstance(what, tuple) else (what, 15)
        deadline = time.time() + timeout

        def poll():
            try:
                done = cond()
            except Exception:
                done = False
            if done or time.time() > deadline:
                if not done:
                    log('timed out waiting')
                return sublime.set_timeout(step, 50)
            sublime.set_timeout(poll, 100)
        poll()
    sublime.set_timeout(step, 0)


def finish():
    save()
    log('done: %d/%d passed' % (sum(r['ok'] for r in results), len(results)))
    if CONFIG.get('exit', True):
        for w in sublime.windows():
            for v in w.views():
                v.set_scratch(True)
        sublime.set_timeout(lambda: sublime.run_command('exit'), 300)


def plugin_loaded():
    sublime.set_timeout(lambda: run(main()), 1500)


# ---- helpers --------------------------------------------------------------------------------------------------

def select(view, text, whole_line=False):
    body = view.substr(sublime.Region(0, view.size()))
    a = body.find(text)
    assert a >= 0, 'text not in the buffer: ' + text[:40]
    b = a + len(text)
    if whole_line and body[b:b + 1] == '\n':
        b += 1
    view.sel().clear()
    view.sel().add(sublime.Region(a, b))
    return sublime.Region(a, b)


def buffer(view):
    return view.substr(sublime.Region(0, view.size()))


def main():
    if CONFIG.get('package_control'):
        for g in package_control_steps():
            yield g
        return
    while 'p3n.plugin' not in sys.modules:
        yield 0.2
    m = sys.modules['p3n.plugin']
    api = sys.modules['p3n.lib.api']
    mockmod = importlib.import_module('User.p3n_e2e_mock')
    live = CONFIG.get('live', False)
    shots = CONFIG.get('shots', False)
    mock = None
    if not live:
        mock = mockmod.MockApi()
        api.API = mock.url
        api.POLL_SECONDS = 0.05
    if shots:
        # README pictures: today's live answers, replayed.
        mockmod.FIXTURES.update(mockmod.LIVE)
        mock.free = 9
    log('api ' + api.API)
    window = sublime.active_window()
    place(window)
    path = os.path.join(OUT, 'team-update.md')
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(CONFIG['sample'])
    view = window.open_file(path)
    yield lambda: not view.is_loading()
    view.settings().set('word_wrap', True)
    yield 0.5
    original = buffer(view)
    AI, SHORT = mockmod.AI_TEXT, mockmod.SHORT
    run_ = lambda: m._runs.get(view.id())
    idle = lambda: run_() is not None and not run_().busy
    calls = lambda: len(mock.requests) if mock else 0

    if live or shots:
        for g in live_steps(m, api, window, view, AI, SHORT, original, run_, idle, 'live' if live else 'readme'):
            yield g
        if mock:
            mock.stop()
        return

    # 1. Check for AI: the card, the score, the sentence underlined, the client header.
    view.sel().clear()
    select(view, AI)
    view.run_command('p3n_check')
    check('busy card while checking', run_() and run_().busy)
    yield idle
    r = run_()
    check('check: result', r and r.result and r.result['output']['ai'] == 0.96, r and (r.error and r.error.message))
    marks = view.get_regions('p3n.mark.s0')
    sentence = mockmod.FIXTURES['detect']['output']['sentences'][0]['text']
    check('check: the AI sentence is underlined', marks and view.substr(marks[0]) == sentence, marks and view.substr(marks[0]))
    req = mock.requests[-1]
    check('check: X-P3N-Client', req[2].get('X-P3N-Client') == 'p3n-sublime/' + api.VERSION, req[2].get('X-P3N-Client'))
    check('check: sends the selection', req[3] == {'text': AI}, req[3])
    check('check: free count from the header', m._free == 9, m._free)
    check('check: the selection is untouched', view.sel()[0] == select(view, AI))
    rest(view)
    yield 0.6
    yield capture(window, 'check-dark')

    # 2. A quote in the card jumps to its sentence.
    m.navigate(view, 'p3n:goto:s0')
    check('goto: selects the sentence', view.substr(view.sel()[0]) == sentence)

    # 3. The same card in a light colour scheme.
    view.settings().set('color_scheme', 'Breakers.sublime-color-scheme')
    rest(view)
    yield 0.8
    yield capture(window, 'check-light')
    view.settings().erase('color_scheme')

    # 4. Rewrite, Replace (one undo step), whitespace kept.
    select(view, SHORT, whole_line=True)
    view.run_command('p3n_rewrite')
    yield idle
    r = run_()
    check('rewrite: result', r and r.result and r.skill == 'humanize', r and r.error and r.error.message)
    rest(view)
    yield 0.6
    yield capture(window, 'rewrite-dark')
    m.navigate(view, 'p3n:replace')
    yield 0.3
    want = original.replace(SHORT + '\n', 'This transition helps teams of all sizes.\n')
    check('replace: the rewrite is where the selection was', buffer(view) == want, buffer(view)[:300])
    check('replace: the new text is selected', view.substr(view.sel()[0]) == 'This transition helps teams of all sizes.')
    check('replace: the card says how to undo', r.state.get('replaced') and 'Ctrl+Z' in r.state['note'][0])
    yield 0.4
    yield capture(window, 'replaced')
    view.run_command('undo')
    check('replace: one undo brings the original back', buffer(view) == original)
    m.navigate(view, 'p3n:replace')
    check('replace twice: refused', 'already replaced' in r.state['note'][0])

    # 5. Text changed since the rewrite: Replace refuses.
    select(view, SHORT)
    view.run_command('p3n_rewrite')
    yield idle
    region = view.get_regions('p3n.source')[0]
    view.sel().clear()
    view.sel().add(sublime.Region(region.begin() + 5))
    view.run_command('insert', {'characters': 'X'})
    m.navigate(view, 'p3n:replace')
    r = run_()
    check('changed text: Replace refuses', r.state.get('note') and 'changed since you selected it' in r.state['note'][0], r.state)
    view.run_command('undo')
    check('changed text: buffer as before', buffer(view) == original)

    # 6. Copy and Try another rewrite.
    select(view, SHORT)
    view.run_command('p3n_rewrite')
    yield idle
    copied = []
    real = sublime.set_clipboard
    sublime.set_clipboard = copied.append  # don't touch the user's clipboard
    try:
        m.navigate(view, 'p3n:copy')
    finally:
        sublime.set_clipboard = real
    check('copy: the rewrite, trimmed', copied == ['This transition helps teams of all sizes.'], copied)
    n = calls()
    m.navigate(view, 'p3n:again')
    yield idle
    check('try another: fresh, same text', calls() == n + 1 and mock.requests[-1][3] == {'text': SHORT, 'options': {'fresh': True}}, mock.requests[-1][3])
    m.navigate(view, 'p3n:replace')
    check('try another: Replace still works', 'This transition helps teams' in buffer(view) and SHORT not in buffer(view))
    view.run_command('undo')
    check('try another: one undo', buffer(view) == original)

    # 7. Rewrite from a check card.
    select(view, AI)
    view.run_command('p3n_check')
    yield idle
    m.navigate(view, 'p3n:rewrite')
    yield idle
    check('check card: Rewrite runs on the same text', run_().skill == 'humanize' and mock.requests[-1][3] == {'text': AI})

    # 8. Before sending: too short, nothing selected, two selections.
    n = calls()
    select(view, 'Short text.')
    view.run_command('p3n_check')
    r = run_()
    check('too short: own copy, nothing sent', r.error and 'at least 40 characters' in r.error.message and calls() == n)
    view.run_command('p3n_rewrite')
    r = run_()
    check('rewrite under eight words: own copy, nothing sent',
          r.error and r.error.message == 'Rewrite needs at least a sentence of eight words. Select more.' and calls() == n, r.error and r.error.message)
    # The hint is a popup that closes when the mouse moves, so record what was shown rather than look for it.
    hints = []
    real_hint = m.hint
    m.hint = lambda v, msg: (hints.append(msg), real_hint(v, msg))
    try:
        view.sel().clear()
        view.sel().add(sublime.Region(3))
        view.run_command('p3n_check')
        check('nothing selected: a hint, nothing sent', hints == ['Nothing is selected. Select some text first.'] and calls() == n, hints)
        view.hide_popup()
        view.sel().clear()
        a = select(view, AI)
        view.sel().add(select(view, SHORT))
        view.sel().add(a)
        view.run_command('p3n_rewrite')
        check('two selections: Rewrite asks for one', hints[1:] == ['p3n rewrites one selection at a time. Select one block of text.'] and calls() == n, hints)
        view.hide_popup()
    finally:
        m.hint = real_hint
    view.run_command('p3n_check')
    yield idle
    check('two selections: Check sends both', mock.requests[-1][3] == {'text': AI + '\n\n' + SHORT}, mock.requests[-1][3])

    # 9. The context menu entries show only with a selection.
    view.sel().clear()
    view.sel().add(sublime.Region(3))
    check('context menu hidden without a selection', not m.P3nCheckCommand(view).is_visible(menu=True) and not m.P3nRewriteCommand(view).is_visible(menu=True))
    check('palette entries always there', m.P3nCheckCommand(view).is_visible() and m.P3nRewriteCommand(view).is_visible())
    select(view, AI)
    check('context menu shown with a selection', m.P3nCheckCommand(view).is_visible(menu=True))

    # 10. Errors, each with its own copy; the status bar shows the card's first line.
    for name, skill, reply in mockmod.ODD:
        mock.queue = [reply]
        select(view, AI)
        statuses = []
        real_status = sublime.status_message
        sublime.status_message = lambda msg: (statuses.append(msg), real_status(msg))
        view.run_command('p3n_check' if skill == 'detect' else 'p3n_rewrite')
        yield idle
        sublime.status_message = real_status
        r = run_()
        check('error: ' + name, r.error is not None and r.result is None, r.error and r.error.message)
        check('error: ' + name + ': status bar', r.error and statuses == ['p3n: ' + m.core.error_summary(r.error)], statuses)
        if name == 'free limit':
            html = m.core.error_html(r.skill, r.error)
            check('free limit: Sign up only to p3n.si', 'href="https://p3n.si/register"' in html and 'javascript' not in html)
            check('free limit: the status bar says calls, not the API message',
                  statuses == ['p3n: The free calls for this hour are used up; more in about 10 min.'], statuses)
            rest(view)
            yield 0.5
            yield capture(window, 'free-limit')
    mock.queue = [{'status': 200, 'body': dict(mockmod.FIXTURES['detect'], output={'version': 'detect-v9'})}]
    select(view, AI)
    view.run_command('p3n_check')
    yield idle
    check('error: answer without a score', 'without a result' in run_().error.message)

    # 11. Cancel while busy.
    mock.delay = 2
    select(view, AI)
    view.run_command('p3n_check')
    yield 0.3
    check('cancel: shown in the palette while busy', m.P3nCancelCommand(view).is_visible())
    yield 0.5
    yield capture(window, 'busy')
    view.run_command('p3n_cancel')
    check('cancel: card closed', run_() is None)
    yield 2.5
    check('cancel: the late answer is dropped', run_() is None)
    mock.delay = 0

    # 11b. Refused before sending: the status bar says why, as for a failed call, and doesn't keep an earlier
    # run's "Checking… N s".
    mock.delay = 1.5
    select(view, AI)
    view.run_command('p3n_check')
    yield 0.3
    check('busy: the status bar counts', view.get_status('p3n').startswith('p3n: Checking… '), view.get_status('p3n'))
    select(view, 'Short text.')
    shown = status_messages(lambda: view.run_command('p3n_rewrite'))
    check('refused before sending: the status bar says why',
          shown == ['p3n: Rewrite needs at least a sentence of eight words. Select more.'], shown)
    check('refused before sending: the earlier busy line is gone', view.get_status('p3n') == '', view.get_status('p3n'))
    yield 2
    check('refused before sending: the earlier answer is dropped',
          run_().error and run_().error.type == 'too_short' and view.get_status('p3n') == '', view.get_status('p3n'))
    mock.delay = 0

    # 11c. A rewrite past 8 s: the status bar says it takes up to a minute, as the card does.
    mock.delay = 2
    select(view, SHORT)
    view.run_command('p3n_rewrite')
    run_().started -= 9
    yield 1.2
    line = view.get_status('p3n')
    check('long rewrite: the status bar says it takes up to a minute',
          line.startswith('p3n: Rewriting… ') and line.endswith(' s (a rewrite takes up to a minute)'), line)
    yield idle
    check('long rewrite: the busy line goes with the answer', run_().result and view.get_status('p3n') == '', view.get_status('p3n'))
    mock.delay = 0

    # 12. Read-only file: no Replace button.
    select(view, SHORT)
    view.run_command('p3n_rewrite')
    yield idle
    view.set_read_only(True)
    m.render(run_())
    m.navigate(view, 'p3n:replace')
    check('read-only: Replace refuses', 'read-only' in run_().state['note'][0])
    view.set_read_only(False)

    # 13. API key: checked before saving, sent as a bearer token, removable.
    cmd = m.P3nSetApiKeyCommand(window)
    cmd.check('p3n_wrong')
    yield 1
    check('wrong key: not saved', not m.settings().get('api_key'))
    cmd.check('not-a-key')
    check('malformed key: not saved', not m.settings().get('api_key'))
    cmd.check('p3n_good')
    yield lambda: m.settings().get('api_key') == 'p3n_good'
    check('good key: saved', m.settings().get('api_key') == 'p3n_good')
    select(view, AI)
    view.run_command('p3n_check')
    yield idle
    check('keyed call: bearer token', mock.requests[-1][2].get('Authorization') == 'Bearer p3n_good')
    m.P3nRemoveApiKeyCommand(window).run()
    check('remove key', not m.settings().get('api_key'))

    # With no file open, the key commands answer in the status bar.
    class NoFile:
        def active_view(self):
            return None
    shown = status_messages(lambda: m.P3nSetApiKeyCommand(NoFile()).check('not-a-key'))
    check('no file open: a malformed key is explained', shown == ['p3n: Keys start with p3n_. Copy the whole key from the API keys page.'], shown)
    m.settings().set('api_key', 'p3n_good')
    shown = status_messages(lambda: m.P3nRemoveApiKeyCommand(NoFile()).run())
    check('no file open: Remove API Key says so', not m.settings().get('api_key') and shown[:1] and shown[0].startswith('p3n: Key removed'), shown)

    # 14. Clear closes the card and the underlines.
    view.run_command('p3n_clear')
    check('clear: card and underlines gone', run_() is None and not view.get_regions('p3n.mark.s0') and not view.get_regions('p3n.source'))
    check('buffer unchanged at the end', buffer(view) == original)
    mock.stop()


def status_messages(fn):
    """Runs fn() and returns what it showed with sublime.status_message."""
    shown = []
    real = sublime.status_message
    sublime.status_message = lambda msg: (shown.append(msg), real(msg))
    try:
        fn()
    finally:
        sublime.status_message = real
    return shown


def rest(view):
    # The caret at the end of the file, out of the way of the card.
    view.sel().clear()
    view.sel().add(sublime.Region(view.size()))


def keep(name, r):
    with open(os.path.join(OUT, name + '.json'), 'w', encoding='utf-8') as f:
        json.dump(r.result, f, indent=1, ensure_ascii=False)


def package_control_steps():
    # Package Control: Add Repository with the raw repository.json URL, then Install Package > p3n, through the same
    # calls the input box and the quick panel make. Talks to GitHub; makes no p3n API calls (the checks use the mock).
    url = CONFIG['repository']
    tasks_module = 'Package Control.package_control.package_tasks'
    yield (lambda: tasks_module in sys.modules, 60)
    check('Package Control loaded', tasks_module in sys.modules)
    check('p3n not installed before', 'p3n.plugin' not in sys.modules)
    sublime.run_command('add_repository', {'url': url})
    repos = sublime.load_settings('Package Control.sublime-settings').get('repositories') or []
    check('Add Repository: URL saved', url in repos, repos)

    state = {}

    def work():
        try:
            runner = sys.modules[tasks_module].PackageTaskRunner()
            tasks = runner.create_package_tasks(actions=(runner.INSTALL, runner.OVERWRITE))
            names = [t.package_name for t in tasks]
            state['count'] = len(names)
            state['p3n-sublime listed'] = 'p3n-sublime' in names
            pick = [t for t in tasks if t.package_name == 'p3n']
            if pick:
                item = runner.render_quick_panel_items(pick)[0]
                state['item'] = item if isinstance(item, list) else [item.trigger, item.details, item.annotation]
                state['version'] = str(pick[0].available_version)  # a PEP440Version
                # What picking it in the quick panel runs.
                runner.run_install_tasks(pick)
        except Exception:
            state['error'] = traceback.format_exc()
        state['done'] = True
    threading.Thread(target=work, daemon=True).start()
    yield (lambda: state.get('done'), 300)
    log('listed %s packages; p3n item %r, version %s' % (state.get('count'), state.get('item'), state.get('version')))
    check('Install Package lists p3n', state.get('item'), state.get('error') or state)
    version = CONFIG['version']
    check('the version is the latest tag, ' + version, state.get('version') == version, state.get('version'))
    check('no "p3n-sublime" in the list', not state.get('p3n-sublime listed'))
    yield (lambda: 'p3n.plugin' in sys.modules, 60)
    check('the installed package loads', 'p3n.plugin' in sys.modules)

    installed = sublime.installed_packages_path()
    check('installed as p3n.sublime-package', os.path.isfile(os.path.join(installed, 'p3n.sublime-package')), os.listdir(installed))
    check('nothing named p3n-sublime', not any('p3n-sublime' in f for f in os.listdir(installed) + os.listdir(sublime.packages_path())))
    import zipfile
    with zipfile.ZipFile(os.path.join(installed, 'p3n.sublime-package')) as z:
        files = z.namelist()
        meta = json.loads(z.read('package-metadata.json').decode('utf-8')) if 'package-metadata.json' in files else {}
    log('package files: ' + ', '.join(sorted(files)))
    check('package-metadata: version ' + version, meta.get('version') == version, meta)
    check('has the plugin, lib and .python-version', {'plugin.py', 'lib/api.py', 'lib/core.py', '.python-version'} <= set(files), files)
    check('export-ignored files left out', not [f for f in files if f.split('/')[0] in ('tests', 'dev', 'screenshots', '.github', 'repository.json')], files)
    check('settings resolve under Packages/p3n', 'Packages/p3n/p3n.sublime-settings' in sublime.find_resources('p3n.sublime-settings'))
    check('commands in the palette file', 'Packages/p3n/Default.sublime-commands' in sublime.find_resources('Default.sublime-commands'))

    # The installed (zipped) package works: a check, a rewrite, Replace and one undo, against the mock.
    m = sys.modules['p3n.plugin']
    api = sys.modules['p3n.lib.api']
    mockmod = importlib.import_module('User.p3n_e2e_mock')
    mock = mockmod.MockApi()
    api.API = mock.url
    window = sublime.active_window()
    place(window)
    path = os.path.join(OUT, 'team-update.md')
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(CONFIG['sample'])
    view = window.open_file(path)
    yield lambda: not view.is_loading()
    view.settings().set('word_wrap', True)
    original = buffer(view)
    run_ = lambda: m._runs.get(view.id())
    idle = lambda: run_() is not None and not run_().busy
    select(view, mockmod.AI_TEXT)
    view.run_command('p3n_check')
    yield idle
    check('installed: Check for AI', run_().result and run_().result['output']['ai'] == 0.96)
    check('installed: X-P3N-Client', mock.requests[-1][2].get('X-P3N-Client') == 'p3n-sublime/' + version)
    rest(view)
    yield 0.6
    yield capture(window, 'installed-check')
    select(view, mockmod.SHORT)
    view.run_command('p3n_rewrite')
    yield idle
    m.navigate(view, 'p3n:replace')
    yield 0.3
    check('installed: Rewrite and Replace', run_().state.get('replaced') and mockmod.SHORT not in buffer(view))
    view.run_command('undo')
    check('installed: one undo', buffer(view) == original)
    messages = [v for w in sublime.windows() for v in w.views() if v.name() == 'Package Control Messages']
    log('Package Control Messages views: %d' % len(messages))
    if messages:
        check('install message shown', 'Thanks for installing p3n' in messages[0].substr(sublime.Region(0, messages[0].size())))
    mock.stop()


def live_steps(m, api, window, view, AI, SHORT, original, run_, idle, tag):
    # Live: exactly two keyless calls to api.p3n.si, one check and one rewrite. Readme: the same steps on the mock.
    select(view, AI)
    view.run_command('p3n_check')
    yield (idle, 120)
    r = run_()
    check(tag + ' check', r and r.result is not None, r and r.error and (r.error.type + ': ' + r.error.message))
    if r and r.result:
        keep(tag + '-check', r)
        o = r.result['output']
        log('live check: ai=%s label=%s version=%s sentences=%d free=%s' % (o.get('ai'), o.get('label'), o.get('version'), len(o.get('sentences') or []), m._free))
        check(tag + ' check: sentences underlined', any(view.get_regions('p3n.mark.s%d' % i) for i in range(5)) or not o.get('sentences'))
    rest(view)
    yield 1
    yield capture(window, tag + '-check')
    view.settings().set('color_scheme', 'Breakers.sublime-color-scheme')
    yield 1
    yield capture(window, tag + '-check-light')
    view.settings().erase('color_scheme')
    select(view, SHORT, whole_line=True)
    view.run_command('p3n_rewrite')
    yield (idle, 150)
    r = run_()
    check(tag + ' rewrite', r and r.result is not None, r and r.error and (r.error.type + ': ' + r.error.message))
    if r and r.result:
        keep(tag + '-rewrite', r)
        o = r.result['output']
        log('live rewrite: %r ai_before=%s ai_after=%s version=%s free=%s' % (o.get('text'), o.get('ai_before'), o.get('ai_after'), o.get('version'), m._free))
        rest(view)
        yield 1
        yield capture(window, tag + '-rewrite')
        m.navigate(view, 'p3n:replace')
        yield 0.5
        check(tag + ' replace', r.state.get('replaced') and SHORT not in buffer(view))
        yield capture(window, tag + '-replaced')
        view.run_command('undo')
        check(tag + ' undo', buffer(view) == original)
