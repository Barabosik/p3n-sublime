# Privacy

The p3n package for Sublime Text sends text to p3n only when you ask it to.

- **What it sends.** The text you selected, when you run p3n: Check for AI or p3n: Rewrite, or click Rewrite or Try another rewrite on a result. If you added an API key, the key goes with it so p3n can bill your account. p3n: Set API Key sends the key alone, to check it before saving it. Each call also names the package and its version (for example `p3n-sublime/0.1.3`), so your usage page can show which tool made it.
- **Where it goes.** Only to `api.p3n.si`, over HTTPS.
- **What it doesn't do.** It reads nothing until you run a command, and never reads other files or other parts of the file. It keeps no history, and has no analytics or telemetry.
- **What stays on your computer.** Your API key, in Sublime's settings file `Packages/User/p3n.sublime-settings`, or the `P3N_API_KEY` environment variable if you use that instead. The settings file is plain text. If you sync or publish your Sublime settings, keep that file out. p3n: Remove API Key deletes the key from it.

What p3n does with the text it receives:
- It keeps the text for up to 7 days, to return results and debug failures, then deletes it.
- It sends the text through OpenRouter to the AI model providers that run the check or rewrite.
- It logs the time and IP address of each call. That's how the free limit works.
- It doesn't sell data or use your text to train models.

The full policy is at [p3n.si/privacy](https://p3n.si/privacy). Questions go to [p3n.si/contact](https://p3n.si/contact).
