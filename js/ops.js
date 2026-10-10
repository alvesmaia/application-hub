/* Operações da plataforma — implantações, containers, backups e SDK. */
(function () {
  const baseAdminContent = window.adminContent;
  const appName = key => (applications[key] ? applications[key].name : key);
  const esc = v => String(v).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

  const header = (title, subtitle, action = '') => `<div class="app-page-header"><div><div class="eyebrow" style="color:#1a5983">ADMINISTRAÇÃO · OPERAÇÕES</div><h1 class="page-title">${title}</h1><p class="page-subtitle">${subtitle}</p></div>${action}</div>`;
  const pill = (label, tone) => `<span class="status-pill ops-pill-${tone}">${label}</span>`;
  const toneFor = s => /Implantado|Concluído|Aprovado|Em execução|Válido/.test(s) ? 'ok' : /Validando|Em andamento|Aguardando/.test(s) ? 'warn' : /Rejeitado|Falhou/.test(s) ? 'err' : 'muted';
  const bar = (used, total, tone = '') => `<span class="ops-bar ${tone}"><span style="width:${Math.min(100, Math.round((used / total) * 100))}%"></span></span>`;

  /* ---------- Dados mockados ---------- */
  let deploys = loadCache('deploys', [
    ['app1', '2.4.1', 'Produção', 'GitHub Actions', 'a91f3c2', 'Implantado', 'há 3 h'],
    ['app2', '1.8.0', 'Staging', 'GitHub Actions', '4be07d1', 'Implantado', 'há 5 h'],
    ['app3', '3.1.0', 'Produção', 'Import manual', '—', 'Aguardando aprovação', 'ontem'],
    ['app2', '1.7.9', 'Produção', 'GitHub Actions', '9c21aa0', 'Implantado', '2 dias'],
    ['app1', '2.4.0', 'Staging', 'GitHub Actions', 'f02b6e9', 'Rejeitado', '3 dias']
  ]);
  const validationSteps = [
    ['Manifest', 'Schema, rotas sem conflito e permissões declaradas'],
    ['Assinatura', 'Pacote e imagem assinados por publicador autorizado (cosign)'],
    ['Segurança', 'Scan de vulnerabilidades, SBOM e ausência de segredos'],
    ['Contrato', 'Health check, rota de entrada e versão do SDK compatível'],
    ['Front-end', 'CSS escopado, sem globais JS, CSP compatível'],
    ['Smoke test', 'Sobe em staging e o hub renderiza a rota de entrada'],
    ['Aprovação', 'Gate manual para produção e registro em auditoria']
  ];
  const containers = [
    { app: 'app1', image: 'ghcr.io/org/ativos@sha256:3f9a…c21', cpu: [0.42, 1], mem: [610, 1024], pids: [38, 200], status: 'Em execução', sandbox: 'runc' },
    { app: 'app2', image: 'ghcr.io/org/fornecedores@sha256:b7e0…9d4', cpu: [0.18, 0.5], mem: [402, 512], pids: [21, 100], status: 'Em execução', sandbox: 'runc' },
    { app: 'app3', image: 'ghcr.io/org/servicos@sha256:61cd…07a', cpu: [0.66, 1], mem: [880, 2048], pids: [54, 200], status: 'Em execução', sandbox: 'gVisor' }
  ];
  const host = { cpu: 8, mem: 32, platformCpu: 2, platformMem: 6, systemCpu: 0.5, systemMem: 2 };
  const backupPolicies = [
    ['app1', 'Diário 02:00', '7d · 4s · 6m', ['data', 'uploads'], true, 'S3 · backups-prod'],
    ['app2', 'Diário 03:00', '7d · 4s · 6m', ['data'], true, 'S3 · backups-prod'],
    ['app3', 'A cada 6 h', '14d · 8s · 12m', ['data', 'anexos'], false, 'Azure Blob · hub-bkp']
  ];
  let backups = loadCache('backups', [
    ['app1', 'Agendado', '1,2 GB', 'Concluído', 'hoje 02:00'],
    ['app3', 'Agendado', '860 MB', 'Concluído', 'hoje 06:00'],
    ['app2', 'Sob demanda', '420 MB', 'Concluído', 'ontem 17:42'],
    ['app3', 'Teste de restore', '860 MB', 'Concluído', 'ontem 04:10'],
    ['app2', 'Agendado', '—', 'Falhou', '2 dias']
  ]);

  /* ---------- Implantações ---------- */
  function deploysPage() {
    const envOpts = ['Staging', 'Produção'].map(e => `<option>${e}</option>`).join('');
    const rows = deploys.map((d, i) => `<tr><td><span class="table-primary">${esc(appName(d[0]))}</span></td><td><code>${esc(d[1])}</code></td><td>${esc(d[2])}</td><td>${d[3] === 'Import manual' ? pill('Import manual', 'muted') : pill('GitHub Actions', 'info')}</td><td><code>${esc(d[4])}</code></td><td>${pill(esc(d[5]), toneFor(d[5]))}</td><td>${esc(d[6])}</td><td class="ops-actions">${d[5] === 'Implantado' && d[2] === 'Staging' ? `<button class="secondary-button" data-ops-promote="${i}">Promover</button>` : ''}${d[5] === 'Aguardando aprovação' ? `<button class="secondary-button" data-ops-approve="${i}">Aprovar</button>` : ''}${d[5] === 'Implantado' && d[2] === 'Produção' ? `<button class="secondary-button" data-ops-rollback="${i}">Rollback</button>` : ''}</td></tr>`).join('');
    return `${header('Implantações', 'Import manual de pacotes .hubapp e deploys via CI/CD passam pelo mesmo pipeline de validação.')}
    <div class="ops-grid-2">
      <section class="panel">
        <div class="panel-heading"><h3>Importar pacote</h3><span class="ops-muted">UI · mesma API do CI</span></div>
        <label class="ops-drop" data-ops-drop><input type="file" accept=".hubapp,.zip" data-ops-file hidden />${svg('plus')}<strong data-ops-file-name>Arraste um .hubapp ou clique para selecionar</strong><small>Pacote assinado com manifest, UI, SBOM e referência da imagem OCI</small></label>
        <div class="ops-inline"><div class="form-field"><label>Ambiente</label><select data-ops-env>${envOpts}</select></div><button class="primary-button" data-ops-import>Validar e implantar</button></div>
        <p class="ops-note">Import manual em produção exige aprovação — o CI continua a fonte da verdade.</p>
      </section>
      <section class="panel">
        <div class="panel-heading"><h3>Pipeline de validação</h3><span class="ops-muted" data-ops-pipeline-status>Aguardando pacote</span></div>
        <ol class="ops-steps" data-ops-steps>${validationSteps.map(([t, d]) => `<li><span class="ops-step-dot"></span><div><strong>${t}</strong><small>${d}</small></div></li>`).join('')}</ol>
      </section>
    </div>
    <section class="panel ops-section">
      <div class="panel-heading"><h3>CI/CD · GitHub Actions</h3><span class="ops-muted">OIDC sem segredo fixo</span></div>
      <div class="ops-grid-2 ops-grid-flat">
        <pre class="ops-code">- uses: actions/checkout@v4
- run: docker build -t ghcr.io/org/minha-app:\${{ github.sha }} . &amp;&amp; docker push …
- uses: hub/setup-cli@v1
- run: hub pack --image ghcr.io/org/minha-app@\${{ steps.push.outputs.digest }}
- run: hub publish minha-app.hubapp --env staging --wait</pre>
        <div><div class="ops-caption">Repositórios confiáveis (OIDC)</div>
          <ul class="ops-list"><li><code>org/ativos</code> · branch <code>main</code> → staging, produção</li><li><code>org/fornecedores</code> · branch <code>main</code> → staging</li><li><code>org/servicos</code> · tags <code>v*</code> → staging, produção</li></ul>
          <div class="ops-caption">Comandos</div><ul class="ops-list"><li><code>hub promote app@1.4.0 --to prod</code></li><li><code>hub rollback app --to 1.3.2</code></li></ul></div>
      </div>
    </section>
    <div class="content-panel ops-section"><table class="data-table"><thead><tr><th>Aplicação</th><th>Versão</th><th>Ambiente</th><th>Origem</th><th>Commit</th><th>Status</th><th>Quando</th><th></th></tr></thead><tbody>${rows}</tbody></table></div>`;
  }

  function runValidation() {
    const list = document.querySelector('[data-ops-steps]'), statusEl = document.querySelector('[data-ops-pipeline-status]');
    const fileName = (document.querySelector('[data-ops-file-name]').dataset.name || 'minha-app-1.0.0.hubapp');
    const env = document.querySelector('[data-ops-env]').value;
    const items = [...list.children];
    items.forEach(li => li.className = '');
    statusEl.textContent = 'Validando ' + fileName;
    const total = env === 'Produção' ? items.length : items.length - 1;
    let i = 0;
    const tick = () => {
      if (!document.body.contains(list)) return;
      if (i > 0) items[i - 1].className = 'done';
      if (i === total) {
        if (env !== 'Produção') items[items.length - 1].className = 'skipped';
        statusEl.textContent = env === 'Produção' ? 'Aguardando aprovação' : 'Aprovado';
        deploys.unshift(['app1', fileName.replace(/^.*?-(\d[\w.]*)\.hubapp$/, '$1').replace(/\.hubapp$/, '') || '1.0.0', env, 'Import manual', '—', env === 'Produção' ? 'Aguardando aprovação' : 'Implantado', 'agora']);
        saveCache('deploys', deploys);
        toast(env === 'Produção' ? 'Pacote validado — aguardando aprovação para produção.' : 'Pacote validado e implantado em staging.');
        setTimeout(render, 900);
        return;
      }
      items[i].className = 'running';
      i++;
      setTimeout(tick, 450);
    };
    tick();
  }

  /* ---------- Containers ---------- */
  function runtimePage() {
    const allocCpu = containers.reduce((s, c) => s + c.cpu[1], 0), allocMem = containers.reduce((s, c) => s + c.mem[1], 0) / 1024;
    const freeCpu = host.cpu - host.platformCpu - host.systemCpu - allocCpu, freeMem = host.mem - host.platformMem - host.systemMem - allocMem;
    const seg = (v, total, cls, label) => `<span class="ops-seg ${cls}" style="flex:${v / total}" title="${label}"></span>`;
    const runtime = state.opsRuntime || 'docker';
    const cards = containers.map(c => `<article class="panel ops-container"><div class="ops-container-top">${iconSquare(applications[c.app])}<div><strong>${esc(applications[c.app].name)}</strong><small><code>hub-app-${c.app}</code></small></div>${pill(c.status, 'ok')}</div>
      <div class="ops-image"><code>${esc(c.image)}</code></div>
      <div class="ops-meter"><span>CPU</span>${bar(c.cpu[0], c.cpu[1])}<em>${c.cpu[0]} / ${c.cpu[1]} vCPU</em></div>
      <div class="ops-meter"><span>Memória</span>${bar(c.mem[0], c.mem[1], c.mem[0] / c.mem[1] > .75 ? 'warn' : '')}<em>${c.mem[0]} / ${c.mem[1]} MiB</em></div>
      <div class="ops-meter"><span>Processos</span>${bar(c.pids[0], c.pids[1])}<em>${c.pids[0]} / ${c.pids[1]}</em></div>
      <div class="ops-tags"><span>rede isolada</span><span>read-only</span><span>cap-drop ALL</span><span>no-new-privileges</span><span>${c.sandbox}</span></div>
      <div class="ops-actions"><button class="secondary-button" data-ops-toast="Limites de ${esc(applications[c.app].short)} abertos para edição (limitados pela política da plataforma).">Editar limites</button><button class="secondary-button" data-ops-toast="Container hub-app-${c.app} reiniciado.">Reiniciar</button></div></article>`).join('');
    return `${header('Containers', 'Cada aplicação roda em container exclusivo com recursos definidos — compatível com Docker e Podman.')}
    <div class="ops-grid-2">
      <section class="panel"><div class="panel-heading"><h3>Runtime</h3><span class="ops-muted">Docker Engine API</span></div>
        <div class="filter-chips"><button class="filter-chip ${runtime === 'docker' ? 'active' : ''}" data-ops-runtime="docker">Docker</button><button class="filter-chip ${runtime === 'podman' ? 'active' : ''}" data-ops-runtime="podman">Podman</button></div>
        <div class="form-field"><label>HUB_CONTAINER_SOCKET</label><input readonly value="${runtime === 'docker' ? '/var/run/docker.sock' : '/run/podman/podman.sock'}" /></div>
        <p class="ops-note">Somente recursos comuns aos dois runtimes: imagens OCI, limites cgroups v2, redes bridge, volumes e restart policy.</p></section>
      <section class="panel"><div class="panel-heading"><h3>Capacidade do host</h3><span class="ops-muted">admission control</span></div>
        <div class="ops-caption">CPU · ${host.cpu} vCPU</div><div class="ops-stack">${seg(host.systemCpu, host.cpu, 'sys', 'Sistema')}${seg(host.platformCpu, host.cpu, 'plat', 'Plataforma')}${seg(allocCpu, host.cpu, 'apps', 'Aplicações')}${seg(freeCpu, host.cpu, 'free', 'Livre')}</div>
        <div class="ops-caption">Memória · ${host.mem} GiB</div><div class="ops-stack">${seg(host.systemMem, host.mem, 'sys', 'Sistema')}${seg(host.platformMem, host.mem, 'plat', 'Plataforma')}${seg(allocMem, host.mem, 'apps', 'Aplicações')}${seg(freeMem, host.mem, 'free', 'Livre')}</div>
        <div class="ops-legend"><span class="sys">Sistema</span><span class="plat">Plataforma (reservado)</span><span class="apps">Aplicações</span><span class="free">Livre ${freeCpu.toFixed(1)} vCPU · ${freeMem.toFixed(1)} GiB</span></div></section>
    </div>
    <div class="ops-cards ops-section">${cards}</div>`;
  }

  /* ---------- Backups ---------- */
  function backupsPage() {
    const policies = backupPolicies.map(([app, sched, ret, vols, hooks, dest]) => `<tr><td><span class="table-primary">${esc(appName(app))}</span></td><td>${sched}</td><td>${ret}</td><td>${vols.map(v => `<code>${v}</code>`).join(' ')}</td><td>${hooks ? pill('pre/post', 'ok') : pill('crash-consistent', 'warn')}</td><td>${dest}</td><td class="ops-actions"><button class="secondary-button" data-ops-backup="${app}">Backup agora</button></td></tr>`).join('');
    const history = backups.map((b, i) => `<tr><td><span class="table-primary">${esc(appName(b[0]))}</span></td><td>${esc(b[1])}</td><td>${esc(b[2])}</td><td>${pill(esc(b[3]), toneFor(b[3]))}</td><td>${esc(b[4])}</td><td class="ops-actions">${b[3] === 'Concluído' ? `<button class="secondary-button" data-ops-toast="Restauração de ${esc(appName(b[0]))} (${esc(b[4])}) iniciada — imagem, volumes e banco.">Restaurar</button><button class="secondary-button" data-ops-toast="Clone de ${esc(appName(b[0]))} sendo criado em staging.">Clonar p/ staging</button>` : ''}</td></tr>`).join('');
    return `${header('Backups e snapshots', 'Estado das aplicações (volumes, bancos e configuração) com restic, criptografado e fora do host.')}
    <div class="metric-grid"><div class="metric-card"><div class="metric-label">Último teste de restore</div><div class="metric-value">ontem</div><div class="metric-change">Central de Serviços · OK em 4 min</div></div><div class="metric-card"><div class="metric-label">Armazenado (deduplicado)</div><div class="metric-value">38,4 GB</div><div class="metric-change">−62% com deduplicação</div></div><div class="metric-card"><div class="metric-label">Falhas nas últimas 24 h</div><div class="metric-value">1</div><div class="metric-change down">Fornecedores · hook pre expirou</div></div></div>
    <div class="content-panel"><table class="data-table"><thead><tr><th>Aplicação</th><th>Agenda</th><th>Retenção</th><th>Volumes</th><th>Consistência</th><th>Destino</th><th></th></tr></thead><tbody>${policies}</tbody></table></div>
    <div class="content-panel ops-section"><table class="data-table"><thead><tr><th>Aplicação</th><th>Tipo</th><th>Tamanho</th><th>Status</th><th>Quando</th><th></th></tr></thead><tbody>${history}</tbody></table></div>
    <p class="ops-note">Jobs de backup rodam em container auxiliar com CPU, memória e I/O limitados — sem impacto nas demais aplicações.</p>`;
  }

  function startBackup(app) {
    backups.unshift([app, 'Sob demanda', '—', 'Em andamento', 'agora']);
    render();
    toast(`Backup de ${appName(app)} iniciado.`);
    setTimeout(() => { backups[0] = [app, 'Sob demanda', (0.3 + Math.random()).toFixed(1).replace('.', ',') + ' GB', 'Concluído', 'agora']; saveCache('backups', backups); if (location.pathname === '/team/admin/backups') render(); toast(`Backup de ${appName(app)} concluído.`); }, 2200);
  }

  /* ---------- SDK ---------- */
  function sdkPage() {
    const cmd = (c, d) => `<div class="ops-cmd"><code>${c}</code><small>${d}</small></div>`;
    return `${header('SDK e pacotes', 'Contrato para desenvolver, empacotar e publicar aplicações no hub.')}
    <div class="ops-grid-2">
      <section class="panel"><div class="panel-heading"><h3>Pacote .hubapp</h3><span class="ops-muted">zip assinado</span></div>
        <pre class="ops-code">minha-app.hubapp
├── manifest.json   # id, versão, rotas, menu, permissões, variáveis, modo
├── ui/             # Web Component ou assets estáticos
├── runtime.json    # imagem OCI por digest
├── sbom.spdx.json
├── icon.svg
└── signature</pre></section>
      <section class="panel"><div class="panel-heading"><h3>manifest.json</h3><span class="ops-muted">trecho</span></div>
        <pre class="ops-code">{
  "id": "fornecedores", "version": "1.8.0",
  "mode": "fragment",
  "routes": ["/app2/*"],
  "resources": { "cpu": 0.5, "memory": "512Mi", "pids": 100 },
  "env": ["API_BASE_URL", "SMTP_PASSWORD"],
  "backup": { "volumes": ["data"], "schedule": "0 3 * * *" }
}</pre></section>
    </div>
    <section class="panel ops-section"><div class="panel-heading"><h3>CLI</h3><span class="ops-muted">binário único, Windows/Linux/macOS</span></div>
      <div class="ops-cmds">${cmd('hub dev', 'Shell local + proxy para o app nativo, identidade simulada, variáveis de .hub/dev.env')}${cmd('hub dev --limits', 'Aplica CPU/memória/PIDs do manifest (cgroups, Job Objects no Windows sem admin)')}${cmd('hub dev --container', 'Executa em Docker/Podman para fidelidade total')}${cmd('hub pack', 'Gera e assina o .hubapp')}${cmd('hub publish --env staging --wait', 'Publica e aguarda o pipeline de validação')}${cmd('hub promote / rollback', 'Promove a mesma versão ou volta a anterior')}${cmd('hub backup', 'Backup sob demanda e restore')}</div></section>
    <div class="content-panel ops-section"><table class="data-table"><thead><tr><th>Desenvolvimento local</th><th>Igual à produção</th><th>Como cobrir a diferença</th></tr></thead><tbody>
      <tr><td><span class="table-primary">Shell, rotas, identidade, CSP</span></td><td>${pill('Sim', 'ok')}</td><td>—</td></tr>
      <tr><td><span class="table-primary">Variáveis concedidas</span></td><td>${pill('Sim', 'ok')}</td><td>Segredos reais só no staging</td></tr>
      <tr><td><span class="table-primary">CPU / memória / processos</span></td><td>${pill('Aproximado', 'warn')}</td><td><code>hub dev --limits</code></td></tr>
      <tr><td><span class="table-primary">Rede isolada, read-only, banda</span></td><td>${pill('Não', 'err')}</td><td><code>hub dev --container</code> ou staging</td></tr>
    </tbody></table></div>`;
  }

  window.adminContent = function (sub) {
    if (sub === 'deploys') return deploysPage();
    if (sub === 'runtime') return runtimePage();
    if (sub === 'backups') return backupsPage();
    if (sub === 'sdk') return sdkPage();
    return baseAdminContent(sub);
  };

  document.addEventListener('change', e => {
    const input = e.target.closest('[data-ops-file]');
    if (!input || !input.files[0]) return;
    const label = document.querySelector('[data-ops-file-name]');
    label.textContent = input.files[0].name;
    label.dataset.name = input.files[0].name;
  });

  document.addEventListener('click', e => {
    const t = e.target.closest('[data-ops-import],[data-ops-promote],[data-ops-approve],[data-ops-rollback],[data-ops-runtime],[data-ops-backup],[data-ops-toast]');
    if (!t) return;
    e.preventDefault();
    const d = t.dataset;
    if (d.opsImport !== undefined) return runValidation();
    if (d.opsPromote !== undefined) { const r = deploys[+d.opsPromote]; deploys.unshift([r[0], r[1], 'Produção', r[3], r[4], 'Aguardando aprovação', 'agora']); saveCache('deploys', deploys); toast(`${appName(r[0])} ${r[1]} promovida — mesma versão, sem rebuild.`); return render(); }
    if (d.opsApprove !== undefined) { deploys[+d.opsApprove][5] = 'Implantado'; saveCache('deploys', deploys); toast('Implantação aprovada e registrada na auditoria.'); return render(); }
    if (d.opsRollback !== undefined) { const r = deploys[+d.opsRollback]; toast(`Rollback de ${appName(r[0])} para a versão anterior iniciado.`); return; }
    if (d.opsRuntime !== undefined) { state.opsRuntime = d.opsRuntime; return render(); }
    if (d.opsBackup !== undefined) return startBackup(d.opsBackup);
    if (d.opsToast !== undefined) toast(d.opsToast);
  });
  render();
})();
