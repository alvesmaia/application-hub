/* login.js — the login's "Show password" button (the hub's CSP does not allow inline script).
 * Without JavaScript the button stays hidden (hidden attribute) and the field remains a password. */
(function () {
  'use strict';
  const btn = document.querySelector('[data-pw-toggle]');
  const input = document.getElementById('password');
  if (!btn || !input) return;
  btn.hidden = false;
  btn.addEventListener('click', () => {
    const show = input.type === 'password';
    input.type = show ? 'text' : 'password';
    btn.setAttribute('aria-pressed', String(show));
    btn.setAttribute('aria-label', show ? btn.dataset.hideLabel : btn.dataset.showLabel);
    btn.title = btn.getAttribute('aria-label');
    input.focus();
  });
})();
