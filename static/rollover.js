// rollover.js — Session rollover banner (oversized sessions > 50 MiB).
// Polls /api/session/status (lightweight, ~6s) while a session is on screen.
// When the backend reports rollover.status === "proposed", shows a banner with
// a countdown; on timeout (default) or explicit "now", POSTs
// /api/session/rollover {action} and switches to the continuation session.
// "later" dismisses the banner; the backend re-proposes only if the session
// grows past the threshold again.
(function () {
  'use strict';

  const POLL_MS = 6000;
  const BANNER_ID = 'rolloverBanner';
  let _timer = null;
  let _countdownTimer = null;
  let _countdownLeft = 0;
  let _currentSid = null;
  let _resolving = false;

  // Use the app-wide _apiUrl from messages.js when available (it builds an
  // absolute URL from document.baseURI). NOTE: do NOT self-reference this
  // function — the original implementation recursed infinitely here and the
  // banner could never fire a request.
  function _apiUrl(p) {
    if (typeof window._apiUrl === 'function') return window._apiUrl(p);
    return 'api/' + p;
  }

  function _currentSessionId() {
    try {
      if (typeof S !== 'undefined' && S && S.session && S.session.session_id) {
        return S.session.session_id;
      }
    } catch (e) { /* ignore */ }
    return null;
  }

  function _banner() {
    return document.getElementById(BANNER_ID);
  }

  function _ensureBanner() {
    let el = _banner();
    if (el) return el;
    el = document.createElement('div');
    el.id = BANNER_ID;
    el.className = 'rollover-banner';
    el.style.cssText =
      'display:none;position:sticky;top:0;z-index:50;margin:0 auto;max-width:900px;' +
      'background:var(--accent,#3889FD);color:#fff;padding:10px 16px;border-radius:0 0 10px 10px;' +
      'font-size:13px;line-height:1.4;box-shadow:0 2px 10px rgba(0,0,0,.25);';
    const shell = document.getElementById('mainChat') || document.body;
    shell.insertBefore(el, shell.firstChild);
    return el;
  }

  function _clearCountdown() {
    if (_countdownTimer) { clearInterval(_countdownTimer); _countdownTimer = null; }
  }

  function _hideBanner() {
    _clearCountdown();
    const el = _banner();
    if (el) el.style.display = 'none';
  }

  function _showBanner(status) {
    const el = _ensureBanner();
    const threshold = status.threshold_mb || 50;
    const countdown = status.countdown_secs || 60;
    _countdownLeft = countdown;
    el.innerHTML =
      '<div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">' +
      '<span style="font-weight:600">Session &gt; ' + threshold + ' Mo</span>' +
      '<span style="opacity:.92">Cette session devient volumineuse. Continuer dans une nouvelle session avec un résumé ?</span>' +
      '<span id="rolloverCountdown" style="font-variant-numeric:tabular-nums;opacity:.9;min-width:70px;text-align:center"></span>' +
      '<span style="flex:1"></span>' +
      '<button id="rolloverNowBtn" type="button" style="background:#fff;color:#111;border:none;border-radius:6px;padding:5px 12px;font-weight:600;cursor:pointer">Basculer maintenant</button>' +
      '<button id="rolloverLaterBtn" type="button" style="background:transparent;color:#fff;border:1px solid rgba(255,255,255,.6);border-radius:6px;padding:5px 12px;cursor:pointer">Plus tard</button>' +
      '</div>';
    el.style.display = 'block';

    const updateCountdown = function () {
      const c = document.getElementById('rolloverCountdown');
      if (c) c.textContent = 'Bascule auto dans ' + _countdownLeft + 's';
      if (_countdownLeft <= 0) {
        _clearCountdown();
        _resolve('auto');
        return;
      }
      _countdownLeft -= 1;
    };
    updateCountdown();
    _countdownTimer = setInterval(updateCountdown, 1000);

    const nowBtn = document.getElementById('rolloverNowBtn');
    const laterBtn = document.getElementById('rolloverLaterBtn');
    if (nowBtn) nowBtn.onclick = function () { _clearCountdown(); _resolve('now'); };
    if (laterBtn) laterBtn.onclick = function () { _clearCountdown(); _resolve('later'); };
  }

  function _resolve(action) {
    const sid = _currentSid;
    if (!sid || _resolving) return;
    _resolving = true;
    _hideBanner();
    fetch(_apiUrl('api/session/rollover'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify({ session_id: sid, action: action }),
    })
      .then(function (r) { return r.json().catch(function () { return {}; }); })
      .then(function (data) {
        _resolving = false;
        if (data && data.status === 'done' && data.new_session_id) {
          if (typeof showToast === 'function') {
            showToast('Nouvelle session de continuation créée', 4000);
          }
          if (typeof loadSession === 'function') {
            loadSession(data.new_session_id);
          } else if (typeof window.loadSession === 'function') {
            window.loadSession(data.new_session_id);
          }
        } else if (data && data.status === 'deferred') {
          if (typeof showToast === 'function') showToast('Bascule reportée', 2500);
        } else {
          if (typeof showToast === 'function') {
            showToast('Bascule impossible : ' + ((data && data.error) || 'erreur inconnue'), 5000);
          }
        }
      })
      .catch(function () {
        _resolving = false;
        if (typeof showToast === 'function') showToast('Bascule impossible (réseau)', 5000);
      });
  }

  function _poll() {
    const sid = _currentSessionId();
    if (!sid) {
      _currentSid = null;
      _hideBanner();
      return;
    }
    if (sid !== _currentSid) {
      _currentSid = sid;
      _hideBanner();
      _resolving = false;
    }
    fetch(_apiUrl('api/session/status?session_id=' + encodeURIComponent(sid)), {
      credentials: 'same-origin',
    })
      .then(function (r) { return r.json().catch(function () { return {}; }); })
      .then(function (data) {
        const ro = data && data.rollover;
        if (!ro || ro.status !== 'proposed') {
          if (_banner() && _banner().style.display !== 'none' && !_resolving) _hideBanner();
          return;
        }
        if (_banner() && _banner().style.display === 'block') return; // already showing
        _showBanner(ro);
      })
      .catch(function () { /* transient; next poll retries */ });
  }

  function _start() {
    if (_timer) return;
    _poll();
    _timer = setInterval(_poll, POLL_MS);
  }

  // Start once the DOM is ready; re-check on session switches (poll reads the
  // current session id each tick, so no extra wiring is needed).
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', _start);
  } else {
    _start();
  }
})();
