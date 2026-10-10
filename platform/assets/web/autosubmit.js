/* autosubmit.js — envia sozinho o formulário [data-autosubmit] da página (ex.: a
 * confirmação same-origin da entrada no staging, que troca estado só por POST). A CSP do
 * hub não permite script inline; sem JavaScript, o usuário clica no botão do formulário. */
(function () {
  'use strict';
  const form = document.querySelector('form[data-autosubmit]');
  if (!form || form.dataset.autosubmitted) return;
  form.dataset.autosubmitted = '1';
  form.submit();
})();
