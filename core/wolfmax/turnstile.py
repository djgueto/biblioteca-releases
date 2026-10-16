# core/wolfmax/turnstile.py
from __future__ import annotations
import asyncio, json, logging, threading
import nodriver

logger = logging.getLogger(__name__)
SITE_URL = 'https://wolftorrent.com/'
_TIMEOUT = 30

_JS = """
(cfg => {
    window.__tsToken = null;
    window.__tsError = null;
    function render() {
        let el = document.getElementById('__ts');
        if (!el) { el = document.createElement('div'); el.id = '__ts'; document.body.appendChild(el); }
        el.innerHTML = '';
        turnstile.render(el, {
            sitekey: cfg.sitekey, action: cfg.action || undefined, cData: cfg.cdata || undefined,
            callback: function(t) { window.__tsToken = t; },
            'error-callback': function(e) { window.__tsError = 'err:' + e; },
            'timeout-callback': function() { window.__tsError = 'timeout'; }
        });
    }
    if (typeof turnstile !== 'undefined') { render(); }
    else {
        var s = document.createElement('script');
        s.src = 'https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit';
        s.onload = function(){ setTimeout(render, 300); };
        s.onerror = function(){ window.__tsError = 'script_load_failed'; };
        document.head.appendChild(s);
    }
})
"""

class TurnstileSolver:
    def __init__(self):
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True, name='turnstile')
        self._thread.start()
        self._browser = None
        self._page = None

    def solve(self, verification: dict) -> str | None:
        if not verification.get('sitekey'):
            return None
        fut = asyncio.run_coroutine_threadsafe(self._solve(verification), self._loop)
        try:
            return fut.result(timeout=_TIMEOUT + 15)
        except Exception as e:
            logger.warning('Turnstile solver: %s', e)
            return None

    async def _ensure_page(self):
        if self._page:
            return
        self._browser = await nodriver.start(
            headless=False, browser_args=['--window-position=-2000,0', '--window-size=800,600'])
        self._page = await self._browser.get(SITE_URL)
        await self._page.sleep(3)

    async def _solve(self, verification):
        try:
            await self._ensure_page()
        except Exception as e:
            logger.warning('Turnstile: Chrome failed: %s', e)
            self._page = self._browser = None
            return None
        cfg = json.dumps({k: verification.get(k, '') for k in ('sitekey', 'action', 'cdata')})
        await self._page.evaluate(_JS + f'({cfg})')
        for i in range(_TIMEOUT):
            await self._page.sleep(1)
            token = await self._page.evaluate('window.__tsToken')
            error = await self._page.evaluate('window.__tsError')
            if token:
                logger.info('Turnstile resuelto en %ds', i + 1)
                return str(token)
            if error:
                logger.warning('Turnstile: %s', error)
                return None
        logger.warning('Turnstile timeout')
        return None

    def close(self):
        if self._browser:
            try:
                asyncio.run_coroutine_threadsafe(self._stop(), self._loop).result(timeout=5)
            except Exception:
                pass
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)

    async def _stop(self):
        if self._browser:
            self._browser.stop()
            self._browser = self._page = None
