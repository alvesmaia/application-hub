# Design tokens — identidade Alvesmaia

Fonte única do visual da plataforma: **`assets/shell/tokens.css`**, servido em
`/assets/tokens.css` pelo hub-web e pelo `hub dev` e carregado **antes** de `styles.css`.
Os fragmentos dos apps (Rust e Python) são renderizados dentro do mesmo documento do shell,
então usam as mesmas variáveis sem importar nada.

```
tokens.css   → primitivas + semânticas + escalas (espaço, raio, tipo, sombra, camadas, movimento)
theme.css    → tema da instalação (white label): redefine só semânticas (hoje --color-accent)
styles.css   → componentes do shell (mockup), só com var(--…)
hub.css / ops.css / auth.css / demo.css / app.css → idem
```

## Identidade Alvesmaia (cores, fontes, linhas — sem logo)

Fonte única das cores: **`scripts/brand-palette.json`**. Cada **papel** tem um valor claro e
um escuro (grafite) escolhidos à mão; nada é calculado. `node scripts/gen-dark-theme.mjs`
grava em `tokens.css` o bloco `paleta:inicio/fim` (`--am-<papel>` claros + primitivas) e o
bloco `tema-escuro:inicio/fim` (só os `--am-<papel>` escuros); `--check` (nos testes JS e no
`node scripts/gen-dark-theme.mjs --check`) falha se algo estiver desatualizado **ou** se algum par declarado em
`contrast` ficar abaixo de 4,5:1 (texto) / 3:1 (forma, borda de campo) em qualquer tema.

| Papel | Claro | Escuro | Uso |
|---|---|---|---|
| `page` | `#F4F6FB` | `#191919` | fundo da página |
| `surface` / `raised` | `#FFFFFF` | `#202020` / `#272727` | cartões, tabelas, campos / popover, menu, modal |
| `alt` / `hover` | `#F6F8FD` / `#F0F3F8` | `#1E1E1E` / `#252525` | rodapé de lista, zebra / linha em hover e divisória de linha |
| `border` / `border-strong` | `#E1E6F0` / `#C9D2E3` | `#2F2F2F` / `#3D3D3D` | borda de cartão / decorativa forte, barra de rolagem |
| `control` | `#7B8799` | `#6B6B6B` | borda de campo (≥ 3:1) |
| `tint` / `tint-border` | `#EEF1FF` / `#C5D0FF` | `#232733` / `#3A4266` | chip, realce / borda do botão secundário |
| `title` / `body` | `#102A43` / `#334E68` | `#FFFFFF` / `#EDEDED` | títulos / texto corrido |
| `secondary` / `faint` / `ink` | `#52606D` / `#5F6B7A` / `#0F1E2B` | `#A5A5A5` / `#9A9A9A` / `#FFFFFF` | rótulos / discreto / quase preto |
| `primary` / `primary-hover` / `on-primary` | `#3A4FD8` / `#2E3FB8` / `#FFFFFF` | `#8FA3FF` / `#AEBCFF` / `#08122E` | botão primário, links, texto de destaque |
| `fill` | `#3A4FD8` | `#4A64F5` | preenchimento índigo com texto branco nos dois temas (apps) |
| `accent` / `accent-soft` | `#4F6BFF` / `#8FA3FF` | `#4F6BFF` / `#6F86FF` | formas, gráficos, foco — **nunca texto corrido** |
| `ok`, `ok-bg` | `#0B6B3A`, `#DDF5E8` | `#4FC79A`, `#1A2E25` | sucesso (também `ok-border`, `ok-shape`) |
| `warn`, `warn-bg` | `#8A4B00`, `#FFF1D6` | `#E0A661`, `#2E2517` | aviso (também `warn-border`, `warn-shape`) |
| `err`, `err-bg` | `#9B1C1C`, `#FDECEC` | `#F08B7C`, `#33201E` | erro (também `err-border`, `err-shape`, `err-fill`) |
| `neutral-bg` | `#EEF0F4` | `#272727` | selo neutro (texto `secondary`) |
| `chrome` / `chrome-raised` | `#102A43` / `#17365A` | `#1E1E1E` / `#272727` | header + rail / tooltip, toast |
| `chrome-text` / `chrome-text-2` / `chrome-icon` | `#FFFFFF` / `#B8C4D6` / `#C5D0FF` | `#FFFFFF` / `#A5A5A5` / `#C5D0FF` | texto, secundário e ícones sobre o cromo |

Item ativo do rail/sidebar: fundo `rgba(79,107,255,.28)`, texto branco e
`box-shadow: inset 3px 0 0 #4F6BFF` (`--color-chrome-active-bg`, `--color-chrome-active-bar`).
Foco: `outline: 2px solid` `--color-focus` (`#4F6BFF`), deslocamento 2px (-2px dentro de
tabelas, popovers e modais).

**Fontes** (as mesmas do site da Alvesmaia), servidas pelo próprio hub em `/assets/fonts/`
(woff2 variáveis, `font-display: swap`; licença SIL OFL 1.1 em `/assets/fonts/OFL-NunitoSans.txt`
e `/assets/fonts/OFL.txt`). **Nunito Sans** (eixo 400–900, subconjunto latin — cobre os acentos
de pt-BR/es; `format("woff2-variations")`) em toda a interface: `--font-body`, `--font-ui` e
`--font-display` (títulos, KPIs). **Red Hat Display** só no nome da aplicação no header
(`--font-brand`, `.brand-name`). `--font-mono` **Red Hat Mono** (chaves, valores, IDs, código).
O shell pré-carrega Nunito Sans e Red Hat Display; CSP `font-src 'self'`; reservas
`system-ui, -apple-system, "Segoe UI", Helvetica, Arial, sans-serif` e
`ui-monospace, Consolas, monospace`.

**Tamanhos** (px reais — o `html` não tem `zoom`): corpo 13px/400, `letter-spacing: -.005em`;
h1–h6 800, `letter-spacing: -.02em` — h1 26px (22px no cabeçalho de app, 24px em telas baixas),
h2 16px, título de cartão 15px; KPI 27px/800 `tabular-nums`; botões 13px/500, `line-height 1.4`,
`padding 6px 14px`, altura 32px (12px dentro de tabelas); abas/pílulas 13px/500 (28px); selos
12px/600; cabeçalho de tabela 11px/600 MAIÚSCULAS `letter-spacing: .05em` `secondary`; célula
13px, `padding 8px 12px`; itens de navegação 500 (ativo 600), 32px de altura e 2px entre itens;
eyebrow 12px/700, `letter-spacing: .12em`, cor primária. Piso: texto ≥ 12px (exceto o cabeçalho
de tabela), alvos clicáveis ≥ 28px. Header 41px, rail expandido 204px, ícone da marca 34px,
busca centrada na largura total (máx. 440px).

**Linhas e formas**: bordas sempre 1px; cartão com borda `border`, raio 12, **sem sombra**;
linhas de tabela separadas por 1px `hover` (`#F0F3F8`), cabeçalho por 1px `border`; botões,
campos e selects raio 8 (36-40px de altura); linhas clicáveis e itens de menu raio 6; pílulas
e abas raio 16 (30-32px); selos raio 11. Sombra só em elemento flutuante (tooltip, toast,
menu, modal): `0 8px 24px rgba(16,42,67,.16)` (escuro `0 18px 44px rgba(0,0,0,.44)`).
Botão primário cheio (`primary`, texto `on-primary` 700); secundário branco com borda
`tint-border` e texto `primary`; botão de ícone 32-40px quadrado, raio 8. Tabelas densas
(linha ~38-40px, 14px, números à direita com `tabular-nums`), barras de rolagem finas (6px,
polegar `border-strong`). As regras ficam no fim de `hub.css` ("Identidade Alvesmaia"), por
cima das camadas herdadas do mockup.

## Camadas

0. **Papéis**: `--am-<papel>` (tabela acima), gerados de `scripts/brand-palette.json`.
1. **Primitivas de cor**: `--c-<família>-<luminosidade>`, nomes herdados do mockup que o CSS
   antigo ainda usa; hoje cada uma é só um apelido de um papel (`--c-blue-31: var(--am-primary)`)
   ou um valor fixo nos dois temas (`{"fixed": "#hex"}`, ex.: o terminal de logs, escuro
   sempre). O mapeamento está em `primitives` do JSON.
2. **Semânticas**: o que componentes novos devem usar:

| Token | Uso |
|---|---|
| `--color-bg` | fundo da página |
| `--color-surface`, `--color-surface-raised`, `--color-surface-muted`, `--color-surface-sunken`, `--color-surface-hover` | cartões, flutuantes, faixas alternadas, hover |
| `--color-text`, `--color-text-strong`, `--color-text-ink`, `--color-text-secondary`, `--color-text-muted`, `--color-text-subtle` | textos por ênfase |
| `--color-text-inverse`, `--color-border-inverse` | texto/borda brancos (sobre cromo e preenchimentos) |
| `--color-border`, `--color-border-strong`/`--color-border-control`, `--color-divider` | bordas, borda de campo (3:1), divisórias |
| `--color-accent`, `--color-accent-hover`, `--color-on-accent` | primário (links, botões, texto de destaque), **white label** |
| `--color-accent-shape`, `--color-accent-shape-2`, `--color-focus` | formas, gráficos e foco (`#4F6BFF`) |
| `--color-tint`, `--color-tint-border`, `--color-accent-soft` | chips e realces |
| `--color-brand` | cor de título (navy no claro, branco no escuro) |
| `--color-chrome*` | header e rail (fundo, texto, ícones, item ativo) |
| `--color-success/-warning/-danger` (+ `-soft`, `-shape`), `--color-danger-strong`, `--color-info(-soft)`, `--color-neutral-soft` | estados |
| `--focus-ring`, `--focus-ring-color` | anel de foco |
| `--app-color`, `--app-ink` | identidade do app aberto (o `hub.js` publica a partir do catálogo) |

3. **Escalas**

| Grupo | Tokens |
|---|---|
| Espaço | `--space-2 … --space-48` (nome = px; escala fechada 2,4,6,8,10,12,14,16,18,20,24,28,32,40,44,48) |
| Raio | `--radius-2xs` (2) `xs` (4) `sm` (5) `md` (6) `lg` (7) `xl` (8) `2xl` (9) `3xl` (10) `4xl` (11) `5xl` (12) `6xl` (14) `7xl` (20) `pill` `round` |
| Tipografia | `--font-body`/`--font-ui`/`--font-display` (Nunito Sans), `--font-brand` (Red Hat Display, só a marca), `--font-mono`/`--font-mono-alt` (Red Hat Mono); `--font-size-4xs`…`--font-size-sm` (12px, piso) … `--font-size-base` (14px) … `--font-size-7xl` (27px) |
| Sombras | `--shadow-card` (`none`), `--shadow-popover`, `--shadow-modal` (só flutuantes) |
| Camadas | `--z-raised` 1 · `--z-rail` 25 · `--z-header` 30 · `--z-rail-restore` 35 · `--z-popover` 40 · `--z-overlay` 45 · `--z-backdrop` 49 · `--z-sidebar` 50 · `--z-tooltip` 60 · `--z-toast` 70 · `--z-menu` 80 |
| Movimento | `--ease-emphasized`, `--duration-fast`, `--duration-base` (zerados com `prefers-reduced-motion`) |

## White label

Plataforma › Configurações › **Cor da marca**: salva `brand_color` (`#rrggbb`, validado no
servidor — nada além de hex chega ao CSS) e `/assets/theme.css` passa a ter
`:root { --color-accent: <cor>; }`. Tudo que usa o accent (botões primários do shell, links,
foco, eyebrows, destaques dos apps que seguem os tokens) muda na hora, sem rebuild.

## Tema escuro (grafite)

Os valores escuros são **explícitos** (coluna "Escuro" acima, em `scripts/brand-palette.json`);
o gerador só os escreve no bloco `tema-escuro:inicio/fim` de `tokens.css` (não editar à mão).
Vale para `:root[data-theme="dark"]` (escolha do usuário, gravada no servidor e posta no
`<html>` pelo shell) e, quando o usuário escolhe "Automático (sistema)", para
`prefers-color-scheme: dark` (`:root[data-theme="auto"]`). **O padrão é o tema claro**: quem
nunca escolheu (preferência ausente ou o antigo valor vazio) recebe `data-theme="light"` do
servidor, mesmo com o SO escuro — sem flash; `<meta name="color-scheme">` acompanha
(`light` / `dark` / `light dark` no automático). Telas de login/credenciais são sempre claras. Como primitivas e semânticas apontam para `--am-*` por
`var()`, o bloco escuro redefine só os papéis.

- **Cromo**: navy no claro, grafite (`#1E1E1E`) no escuro, com a mesma barra índigo no item
  ativo. Não há mais regra especial "cromo mantém o claro".
- **Apps do mockup**: `app2.css`/`app3.css` não têm cor literal: o gerador trocou cada uma por
  um token do hub (`var(--am-<papel>)`, `color-mix` quando havia transparência), escolhido pela
  propriedade (texto/borda/fundo) e pela cor; cor literal nova nesses arquivos é convertida ao
  rodar o gerador e reprovada no `--check`. A paleta terracota/floresta própria desses apps saiu
  (não passava no contraste dos dois temas); a identidade de cada app segue em `--app-ink`/
  `--app-color` (catálogo; tintas de `fornecedores`/`contratos` escurecidas para 4,5:1).
- **Cor da marca** (white label): no escuro o `theme.css` usa a cor da marca misturada com
  branco (45%), para manter o contraste.
- **Gráficos**: `hub-chart.js` usa a paleta de séries da identidade (`#4F6BFF` primeiro).
- **Contraste**: o `--check` garante os pares declarados; a sonda `tests-e2e/a11y-probe.mjs`
  (axe, dois temas) mede as páginas reais. O antigo `scripts/fix-contrast.mjs` (ajuste
  automático sobre um tema calculado) foi aposentado: ajuste o papel no JSON e rode o gerador.

## Para quem escreve apps

- Use as semânticas (`var(--color-text-muted)`, `var(--space-12)`, `var(--radius-xl)`…) e
  `var(--app-ink)`/`var(--app-color)` para a identidade do seu app.
- O SDK Python (`templates/*.html`) e os fragmentos Rust usam só classes do shell e tokens;
  o CSS próprio de cada app (`demo.css`, `app.css`, `app2.css`, `app3.css`) também.

## Celular e tabelas responsivas (hub)

CSS só do hub (`hub.css`; não é contrato de app — nenhum `data-hub-*` novo):

- **Tabela → cartões** (`.hub-table-cards` na `<table>`, `.hub-cards-panel` no painel).
  ≥ 1024px tabela normal; 768–1023px some `.hub-col-secondary`; < 768px cada `<tr>` vira
  cartão: `td.hub-card-title` (`.hub-avatar` + nome + `.hub-truncate` com `title`) e
  `td.hub-card-actions` (menu "⋯", alvo ≥ 40px) na 1ª linha; demais células com rótulo de
  `data-label`. `.hub-hide-cards` some e `.hub-cards-only` aparece nos cartões (ex.:
  "Editar" dentro do menu). Use `role="table|rowgroup|row|columnheader|cell"` explícitos
  (o `display` muda e o navegador perderia a semântica). Aplicado em Acesso › Usuários.
- **Filtros por coluna** (`<table data-hub-colfilter data-hub-colfilter-empty="…">`, só nas
  telas do hub — `hub-ux.js`; não exposto aos apps): cada `<th data-hub-col="chave"
  data-hub-col-label="Rótulo" [data-hub-col-type="list|date"]>` ganha um funil (cheio quando
  ativo) que abre um popover com ordenar A→Z/Z→A, lista de valores com busca (> 8 valores) e
  "Selecionar todos" — ou intervalo De/Até para datas — e Limpar/Aplicar. Valor da célula:
  `data-hub-value` (datas em ms) ou o texto. Estado na URL (`f.<chave>=a,b`,
  `f.<chave>=AAAA-MM-DD..AAAA-MM-DD`, `sort=<chave>:asc|desc`) — o link é compartilhável.
  Linha "Filtros ativos: … ✕ · Limpar tudo" acima da tabela. < 768px o popover vira folha de
  baixo para cima e o botão "Filtros" lista as colunas (serve ao layout de cartões, sem
  cabeçalho). Aplicado em Operação › Tarefas.
- **Botão com menu** (`details.hub-row-menu.hub-new-menu` com `summary.primary-button`):
  uma ação principal com variações ("Nova conexão ▾"); itens `role="menuitem"`, setas ↑/↓.
- **Menu "⋯"** (`details.hub-row-menu`): `hub-ux.js` vira para cima
  (`.hub-row-menu--up`) ou alinha à esquerda (`--start`) quando sairia da tela.
- **Folha** (`.modal.hub-sheet`): < 768px o modal ocupa a tela, ações fixas no rodapé,
  campos ≥ 44px, uma coluna.
- **Shell < 768px**: sem rail; marca à esquerda e, à direita, busca (ícone; paleta ⌘K em
  tela cheia), notificações e ☰ (`[data-hub-drawer-toggle]`; avatar vai para o menu). O ☰
  abre o `#hub-rail` como painel de largura total de cima para baixo sob o header
  (`body.hub-drawer-open`; usuário no topo e conta/tema/idioma/Sair no fim — blocos
  `.hub-drawer-only`) e vira ✕; foco preso, Esc/fundo/✕/toque num item fecham, rolagem da
  página travada. No app, a barra fixa "☰ App · Seção" (`[data-hub-menu="open"]`, rótulo
  posto pelo hub.js) abre as seções do app como painel logo abaixo dela. E2E
  `tests/22-mobile.spec.ts`; varredura com unidades `admin-*-390`.

## Regressão visual

`tests-e2e/visual/visual.spec.ts` + `playwright.visual.config.ts`: 12 telas (login, início,
catálogo, Administração, Operações, `ativos`, `servicos`) comparadas com a referência gravada
**antes** da extração (tolerância de 1% de pixels; horários relativos e o rail — que ganhou
itens novos na onda 8 — mascarados). Rodar com o hub nativo (`bash scripts/demo.sh` em dados
novos):

```bash
cd platform/tests-e2e
npx playwright test -c playwright.visual.config.ts            # compara
npx playwright test -c playwright.visual.config.ts --update-snapshots  # nova referência
```

Onda 9: referências regravadas (Início novo, header com busca/Inbox, cinzas escurecidos para contraste) e 12 telas novas no tema escuro — 24/24 estáveis em duas rodadas.

Identidade Alvesmaia: referências regravadas de propósito (troca de tema: cores, fontes Red Hat e linhas).

Resultado da onda 8: 11/12 telas idênticas à referência; a de Configurações difere só pelos
campos novos (cor da marca, status pública e painel do cofre). As referências são por
plataforma (`-win32`), por isso a suíte não roda no CI Linux.
