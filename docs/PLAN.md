# Application Platform — Interactive Mockup

## 1. Objetivo

Construir o mockup interativo de uma **Internal Application Platform** que funcione como um portal corporativo único para todas as aplicações desenvolvidas pelo time de tecnologia.

A plataforma possui **um único host**:

```text
https://plataforma.domain.com
```

As aplicações são acessadas como subpaths:

```text
https://plataforma.domain.com/app1
https://plataforma.domain.com/app2
https://plataforma.domain.com/app3
```

A plataforma deve manter seu **header global permanentemente visível**, inclusive enquanto uma aplicação estiver sendo executada.

Cada aplicação possui sua própria interface e **sidebar própria**, mas não possui header global próprio.

O conceito é:

```text
┌─────────────────────────────────────────────────────────────┐
│ PLATFORM HEADER                                             │
│ Logo | Search | Notifications | User                       │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│                 APPLICATION                                 │
│                                                             │
│ ┌───────────────┬─────────────────────────────────────────┐ │
│ │ APP SIDEBAR   │ APP CONTENT                             │ │
│ │               │                                         │ │
│ │ Dashboard     │                                         │ │
│ │ Clientes      │                                         │ │
│ │ Relatórios    │                                         │ │
│ │ Configuração  │                                         │ │
│ │               │                                         │ │
│ └───────────────┴─────────────────────────────────────────┘ │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

---

# 2. Conceito de Shell

A plataforma deve ser implementada visualmente como um **Application Shell**.

O shell contém:

```text
Platform Shell
├── Global Header
├── Global Navigation
└── Application Workspace
    ├── Application Sidebar
    └── Application Content
```

O **Global Header nunca desaparece**.

Quando o usuário entra em:

```text
/app1
```

a plataforma não deve parecer que navegou para outro site.

Deve parecer que o usuário abriu uma aplicação dentro da plataforma.

---

# 3. Estrutura de URL

Utilizar:

```text
/                       → Platform Dashboard

/app1                   → Application 1
/app1/dashboard         → Application 1 Dashboard
/app1/clientes          → Application 1 Clientes
/app1/relatorios        → Application 1 Relatórios

/app2                   → Application 2
/app2/dashboard         → Application 2 Dashboard
/app2/configuracoes     → Application 2 Configurações
```

As URLs devem ser simuladas no frontend.

Não utilizar subdomínios para as aplicações.

Não utilizar:

```text
app1.plataforma.domain.com
app2.plataforma.domain.com
```

O padrão obrigatório é:

```text
plataforma.domain.com/app1
plataforma.domain.com/app2
```

---

# 4. Global Header

O header pertence exclusivamente à plataforma.

Ele deve permanecer presente em todas as páginas.

Exemplo:

```text
┌─────────────────────────────────────────────────────────────┐
│ [LOGO] Application Platform   Search   🔔   Usuário Demo   │
└─────────────────────────────────────────────────────────────┘
```

Elementos:

* Logo da plataforma
* Nome da plataforma
* Busca global
* Notificações
* Usuário
* Menu do usuário

O header não deve ser duplicado pelas aplicações.

---

# 5. Application Workspace

Quando uma aplicação estiver aberta:

```text
┌─────────────────────────────────────────────────────────────┐
│ GLOBAL PLATFORM HEADER                                      │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│ ┌──────────────────┬──────────────────────────────────────┐ │
│ │                  │                                      │ │
│ │ APPLICATION      │ APPLICATION CONTENT                  │ │
│ │ SIDEBAR          │                                      │ │
│ │                  │                                      │ │
│ │ Dashboard        │                                      │ │
│ │ Assets           │                                      │ │
│ │ Requests         │                                      │ │
│ │ Reports          │                                      │ │
│ │ Settings         │                                      │ │
│ │                  │                                      │ │
│ └──────────────────┴──────────────────────────────────────┘ │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

A sidebar da aplicação deve ocupar somente o workspace abaixo do header.

---

# 6. Application Sidebar

Cada aplicação pode possuir seu próprio menu.

Exemplo para `app1`:

```text
APP1
Gestão de Ativos

Dashboard
Ativos
Movimentações
Relatórios
Configurações
```

Exemplo para `app2`:

```text
APP2
Central de Solicitações

Dashboard
Solicitações
Aprovações
Relatórios
Configurações
```

O conteúdo do sidebar deve ser específico da aplicação.

---

# 7. Application Identity

Ao abrir uma aplicação, mostrar claramente sua identidade.

Exemplo:

```text
┌───────────────────────┐
│ ◉                     │
│ Gestão de Ativos      │
│                       │
│ Dashboard             │
│ Ativos                │
│ Movimentações         │
│ Relatórios            │
└───────────────────────┘
```

O usuário deve conseguir identificar:

* Qual aplicação está utilizando.
* Qual seção está acessando.
* Como retornar ao portal.

Adicionar no topo do sidebar:

```text
← Applications
```

---

# 8. Navegação

O fluxo principal deve ser:

```text
Platform Dashboard
       │
       ▼
Applications
       │
       ▼
Application Details
       │
       ▼
Open Application
       │
       ▼
/app1
       │
       ├── /app1/dashboard
       ├── /app1/assets
       ├── /app1/movements
       └── /app1/reports
```

O header permanece igual em todas essas rotas.

---

# 9. Application Launcher

No dashboard da plataforma criar uma área:

```text
My Applications
```

Exemplo:

```text
┌────────────────────┐
│ Gestão de Ativos   │
│ ● Healthy          │
│                    │
│ Open Application → │
└────────────────────┘

┌────────────────────┐
│ Indicadores        │
│ ● Healthy          │
│                    │
│ Open Application → │
└────────────────────┘

┌────────────────────┐
│ Solicitações       │
│ ● Healthy          │
│                    │
│ Open Application → │
└────────────────────┘
```

Ao clicar:

```text
Open Application
```

navegar para:

```text
/app1
```

---

# 10. Application Runtime Mock

O mockup deve simular uma aplicação real sendo executada dentro do shell.

Criar pelo menos três aplicações mockadas:

### App 1 — Gestão de Ativos

```text
/app1
```

Sidebar:

```text
Dashboard
Ativos
Movimentações
Relatórios
```

### App 2 — Indicadores

```text
/app2
```

Sidebar:

```text
Dashboard
Indicadores
Análises
Relatórios
```

### App 3 — Solicitações

```text
/app3
```

Sidebar:

```text
Dashboard
Solicitações
Aprovações
Relatórios
```

As três aplicações devem possuir conteúdos visualmente diferentes.

---

# 11. Regra fundamental de UX

Nunca criar:

```text
Application Header
```

separado do:

```text
Platform Header
```

O resultado deve ser:

```text
Platform Header
       │
       ▼
Application Sidebar + Application Content
```

e não:

```text
Platform Header
Application Header
Application Sidebar
Application Content
```

Isso evita uma interface com dois headers concorrentes.

---

# 12. Layout Responsivo

Desktop:

```text
┌──────────────────────────────────────────┐
│ Platform Header                          │
├──────────────┬───────────────────────────┤
│ App Sidebar  │ App Content               │
│              │                           │
│              │                           │
└──────────────┴───────────────────────────┘
```

Mobile:

```text
┌──────────────────────────┐
│ Platform Header          │
├──────────────────────────┤
│ App Menu                 │
├──────────────────────────┤
│ App Content              │
│                          │
└──────────────────────────┘
```

No mobile, o sidebar da aplicação pode virar um drawer.

---

# 13. Arquitetura do Frontend

Separar explicitamente:

```text
src/
├── app/
│   ├── PlatformShell
│   ├── PlatformHeader
│   └── PlatformRoutes
│
├── applications/
│   ├── ApplicationShell
│   ├── ApplicationSidebar
│   ├── app1/
│   ├── app2/
│   └── app3/
│
├── pages/
│   ├── Dashboard
│   ├── Applications
│   ├── Deployments
│   ├── Environments
│   ├── Secrets
│   └── Users
│
├── components/
├── mock/
├── routes/
└── types/
```

O `PlatformShell` deve controlar:

```text
Global Header
Authentication Context
Global Navigation
Application Context
```

O `ApplicationShell` deve controlar:

```text
Application Sidebar
Application Navigation
Application Content
```

---

# 14. Regra de composição

A estrutura conceitual deve ser:

```tsx
<PlatformShell>

    <PlatformHeader />

    <ApplicationShell>

        <ApplicationSidebar />

        <ApplicationContent />

    </ApplicationShell>

</PlatformShell>
```

Nunca:

```tsx
<Application>
    <Header />
</Application>
```

A aplicação deve ser um módulo executado dentro do shell da plataforma.

---

# 15. Objetivo do Mockup

O mockup deve demonstrar claramente que a plataforma é capaz de:

1. Centralizar aplicações.
2. Manter uma identidade visual única.
3. Manter o header global durante a navegação.
4. Executar aplicações em `/app1`, `/app2`, etc.
5. Permitir que cada aplicação tenha seu próprio sidebar.
6. Manter navegação independente dentro de cada aplicação.
7. Permitir retorno ao catálogo da plataforma.
8. Compartilhar autenticação e contexto do usuário.

---

# 16. Critério visual de sucesso

Ao navegar de:

```text
plataforma.domain.com
```

para:

```text
plataforma.domain.com/app1
```

o usuário deve perceber:

> "Estou abrindo uma aplicação dentro da plataforma."

e não:

> "Fui enviado para outro sistema."

Essa distinção é um requisito central do produto.
