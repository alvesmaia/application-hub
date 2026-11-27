/* hub-kit-menu-drag.js — two more declarative behaviors for the apps (part of hub-kit.js, loaded right
 * after it and before hub-kit-mount.js; documented in docs/CONTRATO-APP.md §25):
 *
 *   dropdown menu   <button data-hub-dropdown="#menu"> + <div id="menu" hidden>: the button shows/hides
 *                   the menu (aria-expanded); a click outside, Escape or a click on an item closes it.
 *                   Split buttons and "⋮" menus keep their own markup and CSS (the menu's position).
 *   drag and drop   item: data-hub-drag="/apps/{app}/…" (the POST of the move) + data-hub-drag-id="12";
 *                   list: data-hub-dropzone + data-hub-drop-name="state" data-hub-drop-value="done"
 *                   [data-hub-drop-body=".cards"] [data-hub-drop-confirm="Text"]; items and lists pair by
 *                   data-hub-drag-group / data-hub-drop-group (default: none). On drop the item moves
 *                   before the item under the pointer and the kit posts `<name>=<value>&before_id=<id>`
 *                   ("" = end of the list); a refused answer puts it back. Classes while dragging:
 *                   `hub-dragging` (item) and `hub-drop-over` (list). */
(function () {
  'use strict';
  const P = window.__hubKitParts;
  if (!P) return;
  const { inScope, appRoot, ref, t, toast, request, confirmDialog } = P;

  // ---- dropdown menu ----
  let openMenu = null;
  function setMenu(btn, show) {
    const menu = ref(btn.getAttribute('data-hub-dropdown'));
    if (!menu || !inScope(menu)) return;
    menu.hidden = !show;
    btn.setAttribute('aria-expanded', String(show));
    openMenu = show ? { btn, menu } : null;
  }
  function closeMenu() { if (openMenu) setMenu(openMenu.btn, false); }
  document.addEventListener('click', (e) => {
    const btn = e.target.closest && e.target.closest('[data-hub-dropdown]');
    if (btn && inScope(btn)) {
      e.preventDefault();
      const again = openMenu && openMenu.btn === btn;
      closeMenu();
      if (!again) setMenu(btn, true);
      return;
    }
    if (openMenu) closeMenu(); // outside, or an item of the menu (its own action still runs)
  });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && openMenu) { openMenu.btn.focus(); closeMenu(); } });

  // ---- drag and drop ----
  let dragged = null;
  let home = null;
  const group = (el, attr) => el.getAttribute(attr) || '';
  const zoneAt = (e) => {
    const z = e.target.closest && e.target.closest('[data-hub-dropzone]');
    return z && dragged && group(z, 'data-hub-drop-group') === group(dragged, 'data-hub-drag-group') ? z : null;
  };
  const bodyOf = (z) => (z.getAttribute('data-hub-drop-body') && z.querySelector(z.getAttribute('data-hub-drop-body'))) || z;
  const itemAfter = (body, y) => [...body.children]
    .filter((c) => c !== dragged && c.hasAttribute('data-hub-drag'))
    .find((c) => { const r = c.getBoundingClientRect(); return y < r.top + r.height / 2; }) || null;
  const clearOver = () => document.querySelectorAll('.hub-drop-over').forEach((z) => z.classList.remove('hub-drop-over'));

  document.addEventListener('dragstart', (e) => {
    const it = e.target.closest && e.target.closest('[data-hub-drag]');
    if (!it || !inScope(it)) return;
    dragged = it;
    home = [it.parentNode, it.nextSibling];
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/plain', it.getAttribute('data-hub-drag-id') || '');
    setTimeout(() => it.classList.add('hub-dragging'), 0); // after the browser took the drag image
  });
  document.addEventListener('dragend', () => { if (dragged) dragged.classList.remove('hub-dragging'); dragged = null; clearOver(); });
  document.addEventListener('dragover', (e) => {
    const z = zoneAt(e);
    if (!z) return;
    e.preventDefault();
    if (!z.classList.contains('hub-drop-over')) { clearOver(); z.classList.add('hub-drop-over'); }
  });
  document.addEventListener('drop', (e) => {
    const z = zoneAt(e);
    if (!z) return;
    e.preventDefault();
    clearOver();
    const it = dragged;
    const back = home;
    const next = itemAfter(bodyOf(z), e.clientY);
    const text = z.getAttribute('data-hub-drop-confirm');
    const go = () => move(it, back, z, next);
    if (text) confirmDialog(text, {}).then((ok) => { if (ok) go(); });
    else go();
  });

  async function move(it, back, z, next) {
    bodyOf(z).insertBefore(it, next);
    const form = document.createElement('form');
    form.hidden = true;
    form.setAttribute('data-hub-post', it.getAttribute('data-hub-drag'));
    form.setAttribute('data-hub-swap', 'none');
    const fields = [[z.getAttribute('data-hub-drop-name') || 'zone', z.getAttribute('data-hub-drop-value') || ''],
      ['before_id', next ? next.getAttribute('data-hub-drag-id') || '' : '']];
    for (const [name, value] of fields) {
      const input = document.createElement('input');
      Object.assign(input, { type: 'hidden', name, value });
      form.append(input);
    }
    (appRoot() || document.body).append(form);
    const ok = await request(form);
    form.remove();
    if (ok) return;
    back[0].insertBefore(it, back[1] && back[1].parentNode === back[0] ? back[1] : null);
    toast(t('js.kit_error', 'Não foi possível concluir a operação.'));
  }
})();
