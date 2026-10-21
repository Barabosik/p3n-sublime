"""Checks the package in a real Sublime Text on Windows, against the local stand-in API.

    python dev/e2e.py           # mocked: no calls to api.p3n.si
    python dev/e2e.py --live    # two real keyless calls (one check, one rewrite); counts against this IP's free hour
    python dev/e2e.py --shots   # README pictures: the live run's steps, answered with tests/mock.py's LIVE answers

It copies the installed Sublime Text into dev/portable/ (a Data folder makes it portable, so your own settings,
packages and session are never touched), installs this package there as Packages/p3n, runs dev/e2e_driver.py
inside it, and writes results and screenshots to dev/out/<mode>/. Needs Pillow for the PNGs.
"""
import json
import os
import shutil
import subprocess
import sys
import time

DEV = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(DEV)
INSTALL = os.environ.get('SUBLIME_DIR', r'C:\Program Files\Sublime Text')
PORTABLE = os.path.join(DEV, 'portable')
DATA = os.path.join(PORTABLE, 'Data')
MODE = 'live' if '--live' in sys.argv else 'shots' if '--shots' in sys.argv else 'mock'
OUT = os.path.join(DEV, 'out', MODE)  # one folder per mode, so a mocked run never wipes a live run's pictures
PACKAGE_FILES = ['.python-version', 'plugin.py', 'lib', 'Default.sublime-commands', 'Context.sublime-menu',
                 'Main.sublime-menu', 'p3n.sublime-settings', 'messages.json', 'messages', 'README.md', 'PRIVACY.md', 'LICENSE']

sys.path.insert(0, os.path.join(ROOT, 'tests'))
from mock import AI_TEXT, SHORT  # noqa: E402

SAMPLE = '''# Team update

Intro line.

{ai}

{short}

Short text.

Thanks,
Sam
'''.format(ai=AI_TEXT, short=SHORT)


def prepare(live, shots):
    if not os.path.isfile(os.path.join(PORTABLE, 'sublime_text.exe')):
        print('copying Sublime Text to', PORTABLE)
        shutil.copytree(INSTALL, PORTABLE, ignore=shutil.ignore_patterns('Data', 'unins*', 'update_installer.exe'))
    pkg = os.path.join(DATA, 'Packages', 'p3n')
    user = os.path.join(DATA, 'Packages', 'User')
    shutil.rmtree(pkg, ignore_errors=True)
    os.makedirs(pkg)
    os.makedirs(user, exist_ok=True)
    for name in PACKAGE_FILES:
        src = os.path.join(ROOT, name)
        if os.path.isdir(src):
            shutil.copytree(src, os.path.join(pkg, name), ignore=shutil.ignore_patterns('__pycache__'))
        else:
            shutil.copy2(src, pkg)
    shutil.copy2(os.path.join(DEV, 'e2e_driver.py'), os.path.join(user, 'p3n_e2e.py'))
    shutil.copy2(os.path.join(ROOT, 'tests', 'mock.py'), os.path.join(user, 'p3n_e2e_mock.py'))
    for stale in ('p3n.sublime-settings',):
        if os.path.exists(os.path.join(user, stale)):
            os.remove(os.path.join(user, stale))
    with open(os.path.join(user, 'Preferences.sublime-settings'), 'w', encoding='utf-8') as f:
        json.dump({'update_check': False, 'hot_exit': False, 'remember_open_files': False, 'font_size': 12,
                   'show_definitions': False, 'auto_complete': False}, f)
    shutil.rmtree(os.path.join(DATA, 'Local'), ignore_errors=True)
    shutil.rmtree(OUT, ignore_errors=True)
    os.makedirs(OUT)
    with open(os.path.join(user, 'p3n_e2e.json'), 'w', encoding='utf-8') as f:
        json.dump({'out': OUT, 'live': live, 'shots': shots, 'sample': SAMPLE}, f)


def main():
    live = '--live' in sys.argv
    prepare(live, '--shots' in sys.argv)
    exe = os.path.join(PORTABLE, 'sublime_text.exe')
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 4  # SW_SHOWNOACTIVATE: don't take the user's focus
    proc = subprocess.Popen([exe, '--multiinstance'], startupinfo=si)
    results = os.path.join(OUT, 'results.json')
    deadline = time.time() + (600 if live else 240)
    while time.time() < deadline and not os.path.exists(results):
        time.sleep(0.5)
    time.sleep(2)
    if proc.poll() is None:
        subprocess.run(['taskkill', '/PID', str(proc.pid), '/T', '/F'], capture_output=True)
    if not os.path.exists(results):
        print('no results; log:')
        print(open(os.path.join(OUT, 'log.txt'), encoding='utf-8').read() if os.path.exists(os.path.join(OUT, 'log.txt')) else '(none)')
        sys.exit(1)
    try:
        from PIL import Image
        for f in sorted(os.listdir(OUT)):
            if f.endswith('.bmp'):
                Image.open(os.path.join(OUT, f)).save(os.path.join(OUT, f[:-4] + '.png'))
                os.remove(os.path.join(OUT, f))
    except ImportError:
        print('Pillow not installed: screenshots stay .bmp')
    data = json.load(open(results, encoding='utf-8'))['results']
    for r in data:
        print(('PASS ' if r['ok'] else 'FAIL ') + r['name'] + ('' if r['ok'] else ': ' + r['detail']))
    passed = sum(r['ok'] for r in data)
    print('{}/{} passed'.format(passed, len(data)))
    sys.exit(0 if passed == len(data) and data else 1)


if __name__ == '__main__':
    main()
