/* autosubmit.js — automatically submits the page's [data-autosubmit] form (e.g. the
 * same-origin confirmation of entering staging, which changes state only by POST). The hub's
 * CSP does not allow inline script; without JavaScript, the user clicks the form's button. */
(function () {
  'use strict';
  const form = document.querySelector('form[data-autosubmit]');
  if (!form || form.dataset.autosubmitted) return;
  form.dataset.autosubmitted = '1';
  form.submit();
})();
