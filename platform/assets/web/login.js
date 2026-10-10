/* login.js — botão "Mostrar senha" do login (a CSP do hub não permite script inline).
 * Sem JavaScript o botão fica escondido (atributo hidden) e o campo segue como senha. */
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
