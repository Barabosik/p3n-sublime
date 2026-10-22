# Changelog

## 0.1.2

- Rewrite needs at least a sentence of eight words, and p3n now says so before sending anything, as it already did for Check's 40 characters. Words are counted the way p3n counts them, so numbers don't count.
- When p3n turns down a selection before sending it (too short or too long), the status bar says why, as it does when a call fails. An earlier "Checking… N s" no longer stays there.
- While a rewrite runs past 8 seconds, the status bar also says it takes up to a minute, as the card does.
- **p3n: Remove API Key** says the key is gone even when no file is open, in the status bar.
- README: the minimum lengths, and pictures of the current cards.

## 0.1.1

- When a call fails, the status bar shows the same message as the card, not the API's raw text.
- The free-limit card says "free calls", not "free checks" (rewrites count too), and says to sign up at p3n.si before creating an API key.
- **p3n: Set API Key** names sign-up before the API keys page.
- The rewrite card's note says "the rewrite" instead of "Humanize".
- The install message gives Cmd+Z for a Mac too.

## 0.1.0

First release.

- **p3n: Check for AI**: a score, a one-line reason, and the sentences that give it away, underlined in your text.
- **p3n: Rewrite**, with **Replace selection** as one undo step.
- Results open in a card under your selection, in your colour scheme's colours.
- Free without an account: 10 checks or rewrites an hour, up to 5,000 characters each. An API key from p3n.si lifts the limit.
- Needs Sublime Text 4 (build 4107 or later).
