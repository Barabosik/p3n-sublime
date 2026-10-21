# The package's resource files: valid, pointing at commands that exist, and saying what the code does.
import ast
import json
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(name):
    with open(os.path.join(ROOT, name), encoding='utf-8') as f:
        return f.read()


def load(name):
    """Sublime's JSON: // comments and trailing commas allowed."""
    text = re.sub(r'^\s*//.*$', '', read(name), flags=re.M)
    text = re.sub(r',(\s*[\]}])', r'\1', text)
    return json.loads(text)


def commands(items):
    for item in items:
        if 'command' in item:
            yield item['command'], item.get('args', {})
        for child in item.get('children', []):
            for c in commands([child]):
                yield c


def plugin_commands():
    tree = ast.parse(read('plugin.py'))
    names = set()
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name.endswith('Command'):
            names.add(re.sub(r'(?<!^)(?=[A-Z])', '_', node.name[:-len('Command')]).lower())
    return names


class PackageTest(unittest.TestCase):
    def test_commands_exist(self):
        mine = plugin_commands()
        self.assertEqual(mine, {'p3n_check', 'p3n_rewrite', 'p3n_replace', 'p3n_cancel', 'p3n_clear',
                                'p3n_set_api_key', 'p3n_remove_api_key'})
        for name in ('Default.sublime-commands', 'Context.sublime-menu', 'Main.sublime-menu'):
            for cmd, args in commands(load(name)):
                self.assertTrue(cmd in mine or cmd == 'edit_settings', (name, cmd))
                if cmd == 'edit_settings':
                    self.assertEqual(args['base_file'], '${packages}/p3n/p3n.sublime-settings')

    def test_palette_has_every_user_command(self):
        palette = set(c for c, _ in commands(load('Default.sublime-commands')))
        self.assertEqual(plugin_commands() - palette, {'p3n_replace'})  # Replace lives on the card
        for item in load('Default.sublime-commands'):
            self.assertTrue(item['caption'].startswith('p3n: ') or item['caption'] == 'Preferences: p3n Settings')

    def test_context_menu_is_conditional(self):
        for cmd, args in commands(load('Context.sublime-menu')):
            self.assertEqual(args, {'menu': True}, cmd)

    def test_settings_are_documented(self):
        defaults = load('p3n.sublime-settings')
        used = set(re.findall(r"settings\(\)\.get\('(\w+)'", read('plugin.py')))
        self.assertEqual(used, set(defaults))
        self.assertEqual(defaults['api_key'], '')

    def test_messages(self):
        for key, path in load('messages.json').items():
            self.assertTrue(os.path.isfile(os.path.join(ROOT, path)), path)
        self.assertIn('install', load('messages.json'))

    def test_python_version(self):
        self.assertEqual(read('.python-version').strip(), '3.8')
        self.assertFalse(os.path.exists(os.path.join(ROOT, '__init__.py')))

    def test_version_everywhere(self):
        version = re.search(r"^VERSION = '([\d.]+)'$", read('lib/api.py'), re.M).group(1)
        self.assertRegex(version, r'^\d+\.\d+\.\d+$')
        tag = os.environ.get('GITHUB_REF_NAME', '')
        if os.environ.get('GITHUB_REF_TYPE') == 'tag':
            self.assertEqual(tag, version, 'the tag must match lib/api.py VERSION')

    def test_privacy_matches_the_code(self):
        privacy = read('PRIVACY.md')
        self.assertIn('api.p3n.si', privacy)
        self.assertIn('p3n-sublime/', privacy)
        hosts = set(re.findall(r'https?://([\w.-]+)', read('lib/api.py') + read('plugin.py')))
        self.assertEqual(hosts, {'api.p3n.si', 'p3n.si'})

    def test_no_stray_files(self):
        for dirpath, dirnames, filenames in os.walk(ROOT):
            dirnames[:] = [d for d in dirnames if d not in ('.git', '__pycache__', 'portable', 'out')]
            for f in filenames:
                self.assertFalse(f.endswith('.pyc') or f == 'package-metadata.json', f)


if __name__ == '__main__':
    unittest.main()
