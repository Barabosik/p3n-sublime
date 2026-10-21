# Contributing

## Layout

| Path | What it is |
| --- | --- |
| `plugin.py` | The commands, the card (a phantom) and the underlines. The only module that imports `sublime`. |
| `lib/api.py` | The p3n API over `urllib`: requests, polling, errors. Runs in plain Python. |
| `lib/core.py` | The copy, the checks before sending, and the cards' HTML (minihtml). Runs in plain Python. |
| `*.sublime-*`, `messages/` | Commands, menus, settings and the install message. |
| `tests/` | Unit tests, and `mock.py`, a stand-in for api.p3n.si on 127.0.0.1. |
| `dev/` | The checks in a real Sublime Text (Windows). |

`.python-version` says `3.8`. Builds 4205 and later run it on Python 3.14, and older Sublime Text 4 builds on 3.8, so keep the code 3.8-compatible. Only the standard library is used.

## Tests

```bash
python -m unittest discover -s tests -v
```

They never call the real API. CI runs them on Python 3.8 and 3.14.

## In a real Sublime Text

```bash
python dev/e2e.py
```

It copies the installed Sublime Text (`C:\Program Files\Sublime Text`, or `SUBLIME_DIR`) into `dev/portable/`. A `Data` folder there makes the copy portable, so your own settings and packages are never touched. It installs this package as `Packages/p3n` and runs `dev/e2e_driver.py` inside it.

The driver:
- selects text and runs the commands;
- clicks the card's links through the same callback a click uses;
- checks the buffer, undo and the requests the stand-in API got;
- captures the window with PrintWindow.

It doesn't take focus. Results and screenshots go to `dev/out/mock/`. Needs Pillow.

- `python dev/e2e.py --live` makes two real keyless calls (one check, one rewrite), which count against your IP's free hour. Output goes to `dev/out/live/`, with the raw answers.
- `python dev/e2e.py --shots` retakes the README pictures. It answers with `tests/mock.py`'s `LIVE` runs, so it makes no real calls.

## Package Control review

The channel runs [st_package_reviewer](https://github.com/kaste/st_package_reviewer) on every new package. CI runs it too, on the files Package Control installs: `git archive` leaves out the paths marked `export-ignore` in `.gitattributes`.

```bash
mkdir -p /tmp/p3n && git archive HEAD | tar -x -C /tmp/p3n
uvx --python 3.13 --from git+https://github.com/kaste/st_package_reviewer.git st_package_reviewer /tmp/p3n
```

## Releasing

1. Bump `VERSION` in `lib/api.py`, and add `messages/<version>.txt` plus its line in `messages.json` if users should hear about the change.
2. Commit, then tag the commit with the bare version: `git tag 0.1.1 && git push origin main 0.1.1`. Package Control reads semver tags, and CI fails a tag that doesn't match `VERSION`.
