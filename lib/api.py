# The p3n API (https://p3n.si/docs) over urllib. Calls without a key are free, a few an hour per IP;
# a key from p3n.si/dashboard/keys goes in as a bearer token. Nothing here imports sublime, so the tests
# run it in plain Python. Every function blocks: the plugin calls them from its own thread.
import http.client
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request

VERSION = '0.1.3'
# Every call names this client (X-P3N-Client), for the user's usage page.
CLIENT = 'p3n-sublime/' + VERSION

API = 'https://api.p3n.si'
SITE = 'https://p3n.si'
LINKS = {
    'home': SITE + '/',
    'keys': SITE + '/dashboard/keys',
    'billing': SITE + '/dashboard/billing',
    'signup': SITE + '/register',
    'docs': SITE + '/docs',
    'privacy': SITE + '/privacy',
}
# The API's input limits: without a key, and with one (https://p3n.si/docs).
MAX_CHARS = {'free': 5000, 'key': 200000}
FREE_PER_HOUR = 10
# Detect refuses shorter text (counted after trimming).
MIN_DETECT_CHARS = 40
# Humanize refuses fewer words, counted as the API counts them (core.rewrite_words).
MIN_REWRITE_WORDS = 8

# One request waits this long. The API gives up on a text after 85 s (504 too_long), so this is only a backstop.
TIMEOUT = 100
# Long inputs come back as 202 and are polled: every 2 s, for up to 5 minutes.
POLL_SECONDS = 2
MAX_POLLS = 150

TYPE_BY_STATUS = {401: 'unauthorized', 402: 'insufficient_credit', 403: 'forbidden', 404: 'not_found',
                  422: 'invalid_request', 429: 'rate_limited', 502: 'upstream_error'}
NETWORK = "Can't reach p3n. Check your connection and try again."


class ApiError(Exception):
    def __init__(self, status, type, message, signup_url=None, retry_after=None, free=None):
        super().__init__(message)
        self.status = status
        self.type = type
        self.message = message
        self.signup_url = signup_url
        self.retry_after = retry_after
        self.free = free


class Cancelled(Exception):
    pass


_context = None


def ssl_context():
    # Sublime's Python verifies certificates with OpenSSL's default paths. On Windows those load the system store;
    # on some Linux and macOS setups they point nowhere, so fall back to the OS bundle, or certifi where it's
    # installed. Verification always stays on.
    global _context
    if _context is not None:
        return _context
    ctx = ssl.create_default_context()
    if sys.platform != 'win32' and not ctx.get_ca_certs():
        paths = ssl.get_default_verify_paths()
        found = (paths.cafile and os.path.isfile(paths.cafile)) or (paths.capath and os.path.isdir(paths.capath) and os.listdir(paths.capath))
        if not found:
            for bundle in ('/etc/ssl/certs/ca-certificates.crt', '/etc/pki/tls/certs/ca-bundle.crt',
                           '/etc/ssl/ca-bundle.pem', '/etc/ssl/cert.pem', '/usr/local/etc/openssl/cert.pem'):
                if os.path.isfile(bundle):
                    ctx.load_verify_locations(cafile=bundle)
                    break
            else:
                try:
                    import certifi
                    ctx.load_verify_locations(cafile=certifi.where())
                except ImportError:
                    pass
    _context = ctx
    return ctx


def _number(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def call(path, method='GET', body=None, key=None, timeout=None):
    """One request. Returns (data, status, free); free is X-Free-Remaining, or None on keyed calls."""
    headers = {'Accept': 'application/json', 'X-P3N-Client': CLIENT, 'User-Agent': CLIENT}
    data = None
    if body is not None:
        headers['Content-Type'] = 'application/json'
        data = json.dumps(body, ensure_ascii=False).encode('utf-8')
    if key:
        headers['Authorization'] = 'Bearer ' + key
    req = urllib.request.Request(API + path, data=data, headers=headers, method=method)
    context = ssl_context() if API.startswith('https:') else None
    try:
        res = urllib.request.urlopen(req, timeout=timeout or TIMEOUT, context=context)
        status = res.status
    except urllib.error.HTTPError as e:
        res = e
        status = e.code
    except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError) as e:
        # URLError, timeouts, resets and a connection cut halfway all end here.
        if isinstance(getattr(e, 'reason', e), ssl.SSLCertVerificationError):
            raise ApiError(0, 'network', "Can't reach p3n: the connection to api.p3n.si couldn't be verified. "
                           'A proxy or firewall may be in the way.')
        raise ApiError(0, 'network', NETWORK)
    try:
        raw = res.read()
    except (http.client.HTTPException, OSError):
        raise ApiError(0, 'network', NETWORK)
    finally:
        res.close()
    free = _number(res.headers.get('X-Free-Remaining'))
    try:
        payload = json.loads(raw.decode('utf-8'))
    except ValueError:
        payload = None  # empty, or an HTML error page
    if status >= 400:
        err = payload.get('error') if isinstance(payload, dict) else None
        if not isinstance(err, dict):
            err = {}
        message = err.get('message') or (payload.get('message') if isinstance(payload, dict) else None) \
            or 'p3n answered {}.'.format(status)
        raise ApiError(status, err.get('type') or TYPE_BY_STATUS.get(status, 'error'), message,
                       signup_url=err.get('signup_url'),
                       retry_after=_number(err.get('retry_after_seconds')) or _number(res.headers.get('Retry-After')),
                       free=free)
    return payload, status, free


def _pending(run, status):
    return status == 202 or (isinstance(run, dict) and run.get('status') in ('queued', 'running'))


def run_skill(skill, text, key=None, options=None, cancel=None):
    """Runs a skill on `text` and returns (run, free). `cancel` is a threading.Event; setting it stops the polling
    and the result is dropped (a request already sent still runs on the server)."""
    body = {'text': text}
    if options:
        body['options'] = options
    run, status, free = call('/v1/' + skill, 'POST', body, key)
    polls = 0
    while _pending(run, status) and polls < MAX_POLLS:
        polls += 1
        if cancel is not None:
            if cancel.wait(POLL_SECONDS):
                raise Cancelled()
        else:
            time.sleep(POLL_SECONDS)
        try:
            run, status, _ = call('/v1/runs/' + str(run.get('id')), key=key)
        except ApiError as e:
            # A connection blip doesn't stop the run on the server (it's still charged): keep polling.
            if e.type != 'network':
                raise
    if cancel is not None and cancel.is_set():
        raise Cancelled()
    # Still going after 5 minutes: it may yet finish (and be charged), so don't call it failed.
    if _pending(run, status):
        raise ApiError(504, 'still_running', 'p3n stopped waiting after 5 minutes. The run may still finish and be '
                       'charged. Try a shorter text.')
    if not isinstance(run, dict) or run.get('status') == 'failed' or not run.get('output'):
        raise ApiError(502, 'run_failed', (run.get('error') if isinstance(run, dict) else None)
                       or 'The run failed. You were not charged. Try again.')
    return run, free


def me(key):
    """{ email, balance_usd, key: { name, prefix } }"""
    return call('/v1/me', key=key)[0]
