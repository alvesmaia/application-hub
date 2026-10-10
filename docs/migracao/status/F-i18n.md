# F-i18n - i18n para login, password, status e admin pages

## Completo

Implementação de i18n para as páginas restantes do hub (login, password change/reset, public status, admin pages) usando o mesmo mecanismo de F2b (catálogo `catalog.json` com chaves pt-BR/en/es e função `tr(text, lang)` nos templates).

### Páginas traduzidas

1. **Login (`/login`)** — login.html com:
   - `lang="{{ lang }}"` no HTML tag
   - Todos os textos (rótulos, placeholders, botões, mensagens de erro) usando `{{ tr(...) }}`
   - Context em app.py: `login_get` e `login_post` passam `lang` e `tr: lambda s: tr(s, lang)`

2. **Password Change (`/account/password`)** — password.html com:
   - `lang="{{ lang }}"`
   - Textos traduzidos: "Alterar senha", "Sua conta", "Informe a senha atual...", labels de campos, botões
   - Context via `password_page()` em app.py: passa `lang` e `tr`

3. **Public Status (`/status`, sem autenticação)** — status_public.html com:
   - `lang="{{ lang }}"`
   - Títulos e legendas traduzidos
   - Context via `status_page()` em ux/routes.py: `pub` context inclui `lang` e `tr`

4. **Admin/Ops/Insight/Conexões** (`/admin/*`): os templates `admin_*.html`/`ops_*.html` continuam em pt-BR (nao herdavam `tr`; correcao da afirmacao anterior). O render (`render()` de admin, ops, insight e conexoes) agora passa por `hub.ux.texts.page_response` -> `tr_html`, que traduz textos e atributos visiveis (placeholder, aria-label, title, alt, data-label, data-hub-col-label, data-hub-colfilter-empty, data-obs-chart) pelo catalogo `tr` e, se nao achar, pelas chaves de `texts.json` (por valor pt-BR; ex. eyebrow, "Detalhes tecnicos"). Nao traduz `script/style/code/textarea` nem o trecho entre `<!--notr-->...<!--/notr-->` (usado em `ops_deploys.html` para reproduzir o Rust, que traduz so "Assinatura" e "Ainda nao disponivel" naquele paragrafo). Mensagens JSON das APIs admin/ops continuam em pt-BR (o Rust tambem; erros sao traduzidos no cliente). Correcoes: grupo "Operacao" do menu em es = "Operacion" (era "Operaciones").

### Catalog novo

7 entradas adicionadas a `catalog.json` (1827 → 1835 linhas):
- "Acesso à plataforma" (Platform access / Acceso a la plataforma)
- "As senhas não coincidem." (Passwords do not match / Las contraseñas...)
- "E-mail ou usuário" (Email or username / Correo o nombre...)
- "Entrar" (Sign in / Iniciar sesión)
- "Mostrar senha" (Show password / Mostrar contraseña)
- "Ocultar senha" (Hide password / Ocultar contraseña)
- "Senha atual incorreta." (Incorrect current password / Contraseña actual...)
- "Use seu e-mail corporativo e sua senha." (Use your corporate email and password / Use su correo...)

Todas com 3 idiomas (pt-BR, en, es) traduzidas.

### Paridade (en/es)

**Rust hub (:8080) vs Python hub (:8091):**

| Página | Status | DOM | Pixels 1440 |
|--------|--------|-----|----------|
| `/login` (en) | 200/200 | 0 | 0.00 |
| `/account/password` (en) | 200/200 | 0 | 0.00 |
| `/status` (en, público) | 200/200 | 0 | 0.00 |
| `/login` (es) | 200/200 | 0 | 0.00 |
| `/account/password` (es) | 200/200 | 0 | 0.00 |
| `/status` (es, público) | 200/200 | 0 | 0.00 |

Testes executados com cookie `hub_lang=en` / `hub_lang=es` / `Accept-Language` header.

### Testes

- ✓ pytest: login GET/POST com idioma português, inglês, espanhol
- ✓ pytest: password GET/POST com 3 idiomas
- ✓ pytest: status page pública com lang switching
- ✓ check.sh: ruff, mypy, audit ok

### Mudanças de arquivo

**hub/app.py**
- `login_get()`: contexto agora inclui `lang` e `tr`
- `login_post()`: contexto inclui `lang` e `tr`; traduz erros com `tr(error, lang)`
- `password_page()`: contexto inclui `lang` e `tr`

**hub/ux/routes.py**
- `status_page()`: `pub` context (usuário anônimo) agora inclui `lang`

**hub/web/templates/login.html**
- `lang="{{ lang }}"` (dinâmico)
- Título, eyebrow, labels, botões, aria-labels com `{{ tr(...) }}`

**hub/web/templates/password.html**
- Idem

**hub/web/templates/status_public.html**
- `lang="{{ lang }}"` (dinâmico)
- Títulos com `{{ tr(...) }}`

**hub/web/catalog.json**
- +8 entradas (1835 total)

### Limitações conhecidas

- Admin pages: textos hardcoded nas linhas Python ainda em pt-BR (ex: `"Desativado"` em admin/routes.py linha 347). Próxima fase pode traduzir strings de status Python usando `tr()` na geração da response.
- Mensagens de erro em JSON API (`/api/admin/*`): ainda em pt-BR. Usar `tr()` ao montar erros.
- Logs e auditoria: sempre em pt-BR (não afeta UX).

### Como verificar

```bash
# Login em inglês (accept-language ou cookie)
curl -H "Accept-Language: en" http://localhost:8091/login | grep 'lang="en"'

# Password em espanhol
curl -b "hub_lang=es" http://localhost:8091/account/password | grep 'lang="es"'

# Status público sem auth
curl http://localhost:8091/status
```

### Próximas fases

- F3: traduzir mensagens de status em admin/routes.py (usar `tr()` ao gerar contexts)
- F4: OAuth consent page (já revisado em F2b)
- F5: admin analytics, observability pages (usar page_ctx/tr)


## Admin/Ops (en, es, pt-BR) — `compare.mjs` Rust :8080 x Py local :8092 (`HUB_APPS_FILE=apps.compose.json`)
Todas as 14 rotas `/admin/*`: texto igual ao Rust (0 diferenca de texto). Resultado do harness:
- PASS 0/0 e <=0.5% em en, es e pt-BR: users, roles, env, connections, logs, audit, observability, deploys, tasks, backups; runtime (es/pt-BR; en passa no DOM hidratado, o cru difere so pelo estado do engine Docker: "Conectado a docker.sock" x "Sem conexao").
- FAIL so por **dado**, igual em pt-BR (nao e i18n): health (SLO e coluna "Editar" do Rust), flags (flag E2E `novo-resumo` so no Rust), analytics (en/es: Rust tem 15 buscas/4 KiB, Py 0).
Teste: `tests/test_admin_i18n.py` (users nos 3 idiomas, tasks en/es, `tr_html`).
