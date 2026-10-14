/* hub-i18n.js — translation of what the hub screens' JavaScript writes after the page has
 * loaded (wave 12, item 2.8): toasts, modals, table rows, error messages and
 * `alert`/`confirm`/`prompt`.
 *
 * The server already delivers the page translated (`hub_shell::tr`); this script uses the
 * same catalog (`/assets/i18n/<language>.json`: key = source text in pt-BR, with templates
 * `{…}` for variable parts) and observes the DOM. In pt-BR it does nothing. It never touches
 * `<script>`, `<style>`, `<textarea>`, `<pre>`, `<code>`, field values, or elements with
 * `translate="no"` (user data).
 */
(function () {
  'use strict';

  const ATTRS = ['title', 'aria-label', 'placeholder', 'alt', 'data-confirm', 'data-title',
    'data-label', 'data-empty', 'data-hint', 'data-toast'];
  const SKIP = new Set(['SCRIPT', 'STYLE', 'TEXTAREA', 'PRE', 'CODE', 'NOSCRIPT']);

  // ---- Pure logic (tested in node) ------------------------------------------------
  const MARK = /\{[^{}\s"]{0,24}\}/g;

  function firstWord(s) {
    const m = String(s).match(/[\p{L}\p{N}]+/u);
    return m ? m[0].toLowerCase() : '';
  }

  function compileTemplate(src, dst) {
    const marks = src.match(MARK) || [];
    const lits = src.split(MARK);
    return { lits, marks, dst, weight: lits.join('').length };
  }

  function capture(t, text) {
    const first = t.lits[0];
    const last = t.lits[t.lits.length - 1];
    if (!text.startsWith(first) || !text.endsWith(last) || text.length < first.length + last.length) return null;
    let rest = text.slice(first.length, text.length - last.length);
    const vals = [];
    for (let k = 1; k < t.lits.length; k++) {
      if (k === t.lits.length - 1) {
        if (!rest && last) return null;
        vals.push(rest);
        break;
      }
      const lit = t.lits[k];
      const i = rest.indexOf(lit, 1);
      if (i < 1) return null;
      vals.push(rest.slice(0, i));
      rest = rest.slice(i + lit.length);
    }
    return vals.length === t.marks.length ? vals : null;
  }

  function fill(t, vals) {
    const seen = {};
    return t.dst.replace(MARK, (m) => {
      const k = seen[m] || 0;
      seen[m] = k + 1;
      let n = -1;
      let j = -1;
      for (let i = 0; i < t.marks.length; i++) {
        if (t.marks[i] === m && ++n === k) { j = i; break; }
      }
      return j >= 0 ? vals[j] : m;
    });
  }

  /** Catalog ready to query from the server's JSON. */
  function prepare(c) {
    const exact = new Map(Object.entries(c.exact || {}));
    const templates = (c.templates || []).map(([s, d]) => compileTemplate(s, d))
      .filter((t) => (t.lits.join('').match(/\p{L}/gu) || []).length >= 3)
      .sort((a, b) => b.weight - a.weight);
    const phrases = new Map();
    for (const [k, v] of exact) {
      if ([...k].length < 4) continue;
      const w = firstWord(k);
      if (!phrases.has(w)) phrases.set(w, []);
      phrases.get(w).push([k, v]);
    }
    for (const l of phrases.values()) l.sort((a, b) => b[0].length - a[0].length);
    return { exact, templates, phrases };
  }

  const isWord = (ch) => !!ch && /[\p{L}\p{N}_]/u.test(ch);

  const longPhrase = (k) => k.trim().split(/\s+/).length >= 3;

  function phrasePass(cat, norm, allowShort) {
    let out = '';
    let i = 0;
    let changed = false;
    let leftover = false;
    outer: while (i < norm.length) {
      const ch = norm[i];
      if (/[\p{L}\p{N}]/u.test(ch) && !isWord(norm[i - 1])) {
        const list = cat.phrases.get(firstWord(norm.slice(i)));
        if (list) {
          for (const [k, v] of list) {
            if ((allowShort || longPhrase(k)) && norm.startsWith(k, i) && !isWord(norm[i + k.length])) {
              out += v;
              i += k.length;
              changed = true;
              continue outer;
            }
          }
        }
      }
      if (/\p{L}/u.test(ch)) leftover = true;
      out += ch;
      i++;
    }
    return { out, changed, leftover };
  }

  /** Translates a text (keeps spaces at the edges). */
  function tr(cat, text) {
    if (!cat || typeof text !== 'string') return text;
    const trimmed = text.trim();
    if (!trimmed || !/\p{L}/u.test(trimmed)) return text;
    const lead = text.slice(0, text.length - text.trimStart().length);
    const trail = text.slice(text.trimEnd().length);
    const norm = trimmed.replace(/\s+/g, ' ');
    const hit = cat.exact.get(norm);
    if (hit !== undefined) return lead + hit + trail;
    for (const t of cat.templates) {
      const v = capture(t, norm);
      if (v) {
        // a value that is itself a catalog text is also translated (same rule as the server)
        const vals = v.map((x) => {
          const h = cat.exact.get(x.trim());
          return h === undefined ? x : x.replace(x.trim(), h);
        });
        return lead + fill(t, vals) + trail;
      }
    }
    if (norm.includes(' · ')) {
      const parts = norm.split(' · ');
      const out = parts.map((p) => tr(cat, p));
      if (out.some((p, i) => p !== parts[i])) return lead + out.join(' · ') + trail;
    }
    // Long phrases (3+ words) anywhere; single words only if the whole text
    // gets translated (same rule as the server).
    let r = phrasePass(cat, norm, true);
    if (r.leftover) r = phrasePass(cat, norm, false);
    const { out, changed } = r;
    return changed ? lead + out + trail : text;
  }

  const Pure = { prepare, tr, capture, fill, compileTemplate };
  if (typeof module !== 'undefined' && module.exports) module.exports = Pure;
  if (typeof document === 'undefined') return;

  // ---- DOM --------------------------------------------------------------------------
  const lang = (document.documentElement.getAttribute('lang') || 'pt-BR').toLowerCase();
  if (lang.startsWith('pt')) return;
  const code = 'en';
  let cat = null;
  const done = new WeakMap(); // node → text that we wrote (avoids a loop)

  function skipped(el) {
    for (let e = el; e && e.nodeType === 1; e = e.parentElement) {
      if (SKIP.has(e.tagName) || e.getAttribute('translate') === 'no' || e.isContentEditable) return true;
    }
    return false;
  }

  function textNode(n) {
    if (done.get(n) === n.nodeValue || skipped(n.parentElement)) return;
    const t = tr(cat, n.nodeValue);
    if (t !== n.nodeValue) {
      n.nodeValue = t;
    }
    done.set(n, n.nodeValue);
  }

  function attrs(el) {
    if (skipped(el)) return;
    for (const a of ATTRS) {
      const v = el.getAttribute(a);
      if (v == null || !v) continue;
      const t = tr(cat, v);
      if (t !== v) el.setAttribute(a, t);
    }
    if (el.tagName === 'INPUT' && /^(button|submit|reset)$/i.test(el.type) && el.value) {
      const t = tr(cat, el.value);
      if (t !== el.value) el.value = t;
    }
  }

  function walk(root) {
    if (!root) return;
    if (root.nodeType === 3) { textNode(root); return; }
    if (root.nodeType !== 1 || skipped(root)) return;
    attrs(root);
    const w = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT);
    let n = w.nextNode();
    while (n) {
      if (n.nodeType === 3) textNode(n); else attrs(n);
      n = w.nextNode();
    }
  }

  function observe() {
    new MutationObserver((list) => {
      for (const m of list) {
        if (m.type === 'childList') m.addedNodes.forEach(walk);
        else if (m.type === 'characterData') textNode(m.target);
        else if (m.type === 'attributes' && ATTRS.includes(m.attributeName)) attrs(m.target);
      }
    }).observe(document.documentElement, {
      subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ATTRS,
    });
  }

  // Native dialogs: translates the message (no catalog yet, passes it through as is).
  for (const f of ['alert', 'confirm', 'prompt']) {
    const orig = window[f];
    if (typeof orig !== 'function') continue;
    window[f] = function (msg, ...rest) { return orig.call(window, tr(cat, msg), ...rest); };
  }
  window.hubT = (s) => tr(cat, s);

  fetch(`/assets/i18n/${code}.json`, { credentials: 'same-origin' })
    .then((r) => (r.ok ? r.json() : null))
    .then((c) => {
      if (!c) return;
      cat = prepare(c);
      walk(document.body);
      observe();
    })
    .catch(() => {});
})();
