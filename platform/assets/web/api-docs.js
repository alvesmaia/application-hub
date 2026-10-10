// Documentação da API v1 (onda 11): lê /api/v1/openapi.json (mesma sessão) e desenha as
// operações por grupo. Sem dependências, sem script inline (CSP do hub).
(function () {
  'use strict';
  const root = document.getElementById('apidoc');
  const el = (tag, attrs, ...kids) => {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (k === 'class') e.className = v; else e.setAttribute(k, v);
    }
    for (const k of kids) if (k != null) e.append(k instanceof Node ? k : String(k));
    return e;
  };
  const schemaText = (s) => {
    if (!s) return '';
    if (s.enum) return s.enum.join(' | ');
    if (s.type === 'array') return `${schemaText(s.items)}[]`;
    return s.type || (s.$ref ? s.$ref.split('/').pop() : 'objeto');
  };
  function operation(path, method, op) {
    const d = el('details', { class: 'op', 'data-op': op.operationId });
    const sum = el('summary', {},
      el('span', { class: `method m-${method}` }, method.toUpperCase()),
      el('span', { class: 'path' }, path),
      el('span', { class: 'summary' }, op.summary || ''));
    if (op['x-hub-destructive']) sum.append(el('span', { class: 'badge danger' }, 'destrutiva'));
    if (op['x-hub-mcp'] === false) sum.append(el('span', { class: 'badge' }, 'sem MCP'));
    else sum.append(el('span', { class: 'badge' }, `MCP: ${op.operationId}`));
    d.append(sum);
    const body = el('div', { class: 'op-body' });
    const params = op.parameters || [];
    if (params.length) {
      const t = el('table', {}, el('thead', {}, el('tr', {}, el('th', {}, 'Parâmetro'), el('th', {}, 'Onde'), el('th', {}, 'Tipo'), el('th', {}, 'Descrição'))));
      const tb = el('tbody');
      for (const p of params) tb.append(el('tr', {}, el('td', {}, el('code', {}, p.name), p.required ? ' *' : ''), el('td', {}, p.in), el('td', {}, schemaText(p.schema)), el('td', {}, p.description || '')));
      t.append(tb);
      body.append(t);
    }
    const rb = op.requestBody && op.requestBody.content;
    if (rb) {
      const [ctype, media] = Object.entries(rb)[0];
      body.append(el('p', {}, 'Corpo (', el('code', {}, ctype), '):'), el('pre', {}, JSON.stringify(media.schema, null, 2)));
    }
    const curlPath = path.replace(/\{(\w+)\}/g, ':$1');
    body.append(el('p', {}, 'Exemplo:'), el('pre', {}, `curl -H "Authorization: Bearer $HUB_TOKEN" ${method === 'get' ? '' : `-X ${method.toUpperCase()} `}"${location.origin}${curlPath}"`));
    d.append(body);
    return d;
  }
  fetch('/api/v1/openapi.json', { credentials: 'same-origin' })
    .then((r) => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
    .then((doc) => {
      root.textContent = '';
      const groups = new Map();
      for (const [path, item] of Object.entries(doc.paths || {})) {
        for (const [method, op] of Object.entries(item)) {
          const tag = (op.tags && op.tags[0]) || 'Geral';
          if (!groups.has(tag)) groups.set(tag, []);
          groups.get(tag).push([path, method, op]);
        }
      }
      root.append(el('p', {}, `${doc.info.title} · versão ${doc.info.version} · OpenAPI ${doc.openapi}. Erros no formato RFC 9457 (application/problem+json); listas com limit/offset.`));
      for (const [tag, ops] of groups) {
        root.append(el('h2', { class: 'tag' }, tag));
        for (const [p, m, o] of ops) root.append(operation(p, m, o));
      }
    })
    .catch((e) => { root.textContent = ''; root.append(el('p', { class: 'error' }, `Não foi possível carregar o OpenAPI: ${e.message}`)); });
})();
