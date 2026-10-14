# Application Platform — Interactive Mockup

## 1. Goal

Build the interactive mockup of an **Internal Application Platform** that works as a single corporate portal for all the applications developed by the technology team.

The platform has **a single host**:

```text
https://plataforma.domain.com
```

Applications are accessed as subpaths:

```text
https://plataforma.domain.com/app1
https://plataforma.domain.com/app2
https://plataforma.domain.com/app3
```

The platform must keep its **global header permanently visible**, including while an application is running.

Each application has its own interface and **its own sidebar**, but it does not have its own global header.

The concept is:

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
│ │ Customers     │                                         │ │
│ │ Reports       │                                         │ │
│ │ Settings      │                                         │ │
│ │               │                                         │ │
│ └───────────────┴─────────────────────────────────────────┘ │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

---

# 2. Shell Concept

The platform must be implemented visually as an **Application Shell**.

The shell contains:

```text
Platform Shell
├── Global Header
├── Global Navigation
└── Application Workspace
    ├── Application Sidebar
    └── Application Content
```

The **Global Header never disappears**.

When the user goes to:

```text
/app1
```

the platform must not look like it navigated to another site.

It must look like the user opened an application inside the platform.

---

# 3. URL Structure

Use:

```text
/                       → Platform Dashboard

/app1                   → Application 1
/app1/dashboard         → Application 1 Dashboard
/app1/clientes          → Application 1 Customers
/app1/relatorios        → Application 1 Reports

/app2                   → Application 2
/app2/dashboard         → Application 2 Dashboard
/app2/configuracoes     → Application 2 Settings
```

URLs must be simulated in the frontend.

Do not use subdomains for applications.

Do not use:

```text
app1.plataforma.domain.com
app2.plataforma.domain.com
```

The mandatory pattern is:

```text
plataforma.domain.com/app1
plataforma.domain.com/app2
```

---

# 4. Global Header

The header belongs exclusively to the platform.

It must remain present on every page.

Example:

```text
┌─────────────────────────────────────────────────────────────┐
│ [LOGO] Application Platform   Search   🔔   Demo User      │
└─────────────────────────────────────────────────────────────┘
```

Elements:

* Platform logo
* Platform name
* Global search
* Notifications
* User
* User menu

The header must not be duplicated by the applications.

---

# 5. Application Workspace

When an application is open:

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

The application sidebar must occupy only the workspace below the header.

---

# 6. Application Sidebar

Each application may have its own menu.

Example for `app1`:

```text
APP1
Asset Management

Dashboard
Assets
Movements
Reports
Settings
```

Example for `app2`:

```text
APP2
Request Center

Dashboard
Requests
Approvals
Reports
Settings
```

The sidebar content must be specific to the application.

---

# 7. Application Identity

When opening an application, clearly show its identity.

Example:

```text
┌───────────────────────┐
│ ◉                     │
│ Asset Management      │
│                       │
│ Dashboard             │
│ Assets                │
│ Movements             │
│ Reports               │
└───────────────────────┘
```

The user must be able to identify:

* Which application is being used.
* Which section is being accessed.
* How to return to the portal.

Add at the top of the sidebar:

```text
← Applications
```

---

# 8. Navigation

The main flow must be:

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

The header stays the same across all these routes.

---

# 9. Application Launcher

On the platform dashboard, create an area:

```text
My Applications
```

Example:

```text
┌────────────────────┐
│ Asset Management   │
│ ● Healthy          │
│                    │
│ Open Application → │
└────────────────────┘

┌────────────────────┐
│ Indicators         │
│ ● Healthy          │
│                    │
│ Open Application → │
└────────────────────┘

┌────────────────────┐
│ Requests           │
│ ● Healthy          │
│                    │
│ Open Application → │
└────────────────────┘
```

When clicked:

```text
Open Application
```

navigate to:

```text
/app1
```

---

# 10. Application Runtime Mock

The mockup must simulate a real application running inside the shell.

Create at least three mocked applications:

### App 1 — Asset Management

```text
/app1
```

Sidebar:

```text
Dashboard
Assets
Movements
Reports
```

### App 2 — Indicators

```text
/app2
```

Sidebar:

```text
Dashboard
Indicators
Analyses
Reports
```

### App 3 — Requests

```text
/app3
```

Sidebar:

```text
Dashboard
Requests
Approvals
Reports
```

The three applications must have visually different content.

---

# 11. Fundamental UX Rule

Never create:

```text
Application Header
```

separate from:

```text
Platform Header
```

The result must be:

```text
Platform Header
       │
       ▼
Application Sidebar + Application Content
```

and not:

```text
Platform Header
Application Header
Application Sidebar
Application Content
```

This avoids an interface with two competing headers.

---

# 12. Responsive Layout

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

On mobile, the application sidebar may become a drawer.

---

# 13. Frontend Architecture

Explicitly separate:

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

The `PlatformShell` must control:

```text
Global Header
Authentication Context
Global Navigation
Application Context
```

The `ApplicationShell` must control:

```text
Application Sidebar
Application Navigation
Application Content
```

---

# 14. Composition Rule

The conceptual structure must be:

```tsx
<PlatformShell>

    <PlatformHeader />

    <ApplicationShell>

        <ApplicationSidebar />

        <ApplicationContent />

    </ApplicationShell>

</PlatformShell>
```

Never:

```tsx
<Application>
    <Header />
</Application>
```

The application must be a module running inside the platform shell.

---

# 15. Goal of the Mockup

The mockup must clearly demonstrate that the platform is capable of:

1. Centralizing applications.
2. Maintaining a single visual identity.
3. Keeping the global header during navigation.
4. Running applications at `/app1`, `/app2`, etc.
5. Allowing each application to have its own sidebar.
6. Keeping navigation independent within each application.
7. Allowing a return to the platform catalog.
8. Sharing authentication and user context.

---

# 16. Visual Success Criterion

When navigating from:

```text
plataforma.domain.com
```

to:

```text
plataforma.domain.com/app1
```

the user must perceive:

> "I am opening an application inside the platform."

and not:

> "I was sent to another system."

This distinction is a central product requirement.
