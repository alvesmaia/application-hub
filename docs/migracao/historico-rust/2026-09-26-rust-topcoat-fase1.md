# Application Hub (Rust + Topcoat) — Fase 1: Fundação — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Entregar a fundação do hub em Rust + Topcoat: shell SSR, manifest de apps, registro, gateway com identidade, adaptador de containers (Docker/Podman) e CLI `hub` (`pack`, `dev`).

**Architecture:** Workspace Cargo com crates de domínio puras (testáveis sem framework) e uma camada web fina em Topcoat. O gateway (Axum/Tower) repassa `/<app-id>/*` para o upstream do app, injetando identidade. O runtime fala com a Docker Engine API (Docker nativo ou `podman.socket`) via `bollard`.

**Tech Stack:** Rust stable (edition 2021), Topcoat 0.9, Tokio, Axum 0.8, reqwest, serde/serde_json, semver, bollard, zip, sha2, clap 4, thiserror, anyhow.

**Spec:** `docs/FAQ-ARQUITETURA.md` (itens 1–11) e `docs/PLAN.md` (regras de shell). Mockup de referência visual: `index.html` + `js/` + `css/` (branch `main`).

## Convenção de branches por stack

- Cada stack candidata vive em `stack/<nome>` (esta: `stack/rust-topcoat`). Ex.: `stack/dotnet-blazor`, `stack/ts-node`.
- `main` guarda apenas o mockup e a documentação neutra (FAQ, PLAN).
- Código da stack fica em `platform/` dentro da branch, para não colidir com o mockup.
- Commits sempre na branch da stack; nada da stack é mergeado em `main` até a decisão.

## Global Constraints

- Branch: `stack/rust-topcoat`; todo código em `platform/`.
- Runtime de containers: somente recursos comuns a Docker e Podman (imagens OCI, cgroups v2, redes bridge, volumes, restart policy). Sem Swarm/Quadlet.
- Socket configurável por `HUB_CONTAINER_SOCKET`.
- Identidade para apps: cabeçalho `x-hub-user` (JSON base64url) — em fase 2 vira JWT assinado.
- Cabeçalhos `x-hub-*` vindos do cliente devem ser descartados pelo gateway.
- Limites máximos da política: CPU 4.0, memória 8 GiB, PIDs 1024. Padrões: CPU 0.5, memória 512 MiB, PIDs 100.
- Container de app: `hub-app-<id>`, `read_only`, `cap_drop=ALL`, `no-new-privileges`, rede `hub-app-<id>`.
- `id` de app: `^[a-z][a-z0-9-]{1,31}$`. Rotas: começam com `/<id>/` ou são exatamente `/<id>/*`.
- White label: nenhuma marca fixa no código; nome vem de `HUB_BRAND_NAME` (padrão `Application Hub`).
- Idioma da UI: pt-BR.

## Review Focus

1. Cliente envia `x-hub-user` forjado → gateway deve descartar e usar só a identidade do hub (teste na Task 5).
2. Manifest com `memory: "512MB"` / `"abc"` / `cpu: 0` → erro de validação claro, nunca pânico (teste na Task 2).
3. Mesmo app registrado duas vezes (reimport do mesmo id) → segundo registro rejeitado com erro claro (teste na Task 3).
4. Upstream do app fora do ar → gateway responde 502 com página amigável dentro do shell, sem derrubar o hub (teste na Task 5).
5. Caminho `/app1/../team/admin` → normalizado/rejeitado; nunca escapa para rotas da plataforma (teste na Task 5).

---

## Estrutura de arquivos

```
platform/
├── Cargo.toml                    # workspace
├── rust-toolchain.toml
├── crates/
│   ├── hub-manifest/             # schema + validação do manifest
│   ├── hub-registry/             # registro de apps, resolução de rotas
│   ├── hub-gateway/              # proxy /<id>/* + identidade
│   ├── hub-runtime/              # trait ContainerRuntime + adaptador bollard
│   └── hub-cli/                  # binário `hub` (pack, dev)
├── apps/
│   └── hub-web/                  # shell Topcoat (SSR)
└── docs/SPIKE-TOPCOAT.md         # achados da Task 1
.github/workflows/rust.yml        # CI da stack
```

---

### Task 0: Workspace e CI

**Files:**
- Create: `platform/Cargo.toml`, `platform/rust-toolchain.toml`, `.github/workflows/rust.yml`

**Interfaces:**
- Produces: workspace com membros `crates/*` e `apps/*`.

- [ ] **Step 1: Criar workspace**

`platform/Cargo.toml`:
```toml
[workspace]
resolver = "2"
members = ["crates/*", "apps/*"]

[workspace.package]
edition = "2021"
license = "UNLICENSED"

[workspace.dependencies]
anyhow = "1"
thiserror = "2"
serde = { version = "1", features = ["derive"] }
serde_json = "1"
semver = { version = "1", features = ["serde"] }
tokio = { version = "1", features = ["full"] }
axum = "0.8"
reqwest = { version = "0.12", default-features = false, features = ["rustls-tls"] }
bollard = "0.18"
zip = "2"
sha2 = "0.10"
clap = { version = "4", features = ["derive"] }
base64 = "0.22"
```

`platform/rust-toolchain.toml`:
```toml
[toolchain]
channel = "stable"
components = ["rustfmt", "clippy"]
```

- [ ] **Step 2: CI**

`.github/workflows/rust.yml`:
```yaml
name: rust
on:
  push: { branches: ["stack/rust-topcoat"] }
  pull_request: { branches: ["stack/rust-topcoat"] }
jobs:
  test:
    runs-on: ubuntu-latest
    defaults: { run: { working-directory: platform } }
    steps:
      - uses: actions/checkout@v4
      - uses: dtolnay/rust-toolchain@stable
      - uses: Swatinem/rust-cache@v2
        with: { workspaces: platform }
      - run: cargo fmt --all --check
      - run: cargo clippy --workspace --all-targets -- -D warnings
      - run: cargo test --workspace
```

- [ ] **Step 3: Commit** (o workspace vazio não compila sem membros; commitar junto com a Task 2 se preferir)

```bash
git add platform/Cargo.toml platform/rust-toolchain.toml .github/workflows/rust.yml
git commit -m "chore(rust-topcoat): workspace e CI"
```

---

### Task 1: Spike Topcoat (gate de decisão)

Objetivo: fixar a API real do Topcoat 0.9 que as Tasks 4 e 8 usam. Resultado é documento, não código de produção.

**Files:**
- Create: `platform/apps/hub-web/Cargo.toml`, `platform/apps/hub-web/src/main.rs`, `platform/apps/hub-web/tests/smoke.rs`, `platform/docs/SPIKE-TOPCOAT.md`

**Interfaces:**
- Produces: binário `hub-web` que serve `GET /` e escuta na porta definida por env (nome confirmado neste spike; assumido `PORT`).

- [ ] **Step 1: Teste de fumaça (falha primeiro)**

`platform/apps/hub-web/tests/smoke.rs`:
```rust
use std::{process::{Child, Command}, time::Duration};

struct Server(Child);
impl Drop for Server { fn drop(&mut self) { let _ = self.0.kill(); } }

pub async fn spawn(port: u16) -> Server {
    let child = Command::new(env!("CARGO_BIN_EXE_hub-web"))
        .env("PORT", port.to_string())
        .spawn()
        .expect("spawn hub-web");
    for _ in 0..50 {
        if reqwest::get(format!("http://127.0.0.1:{port}/")).await.is_ok() { break; }
        tokio::time::sleep(Duration::from_millis(100)).await;
    }
    Server(child)
}

#[tokio::test]
async fn home_renders_brand() {
    let _s = spawn(18080).await;
    let body = reqwest::get("http://127.0.0.1:18080/").await.unwrap().text().await.unwrap();
    assert!(body.contains("Application Hub"));
}
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `cd platform && cargo test -p hub-web`
Expected: FAIL (crate/binário inexistente).

- [ ] **Step 3: Implementação mínima (API conforme docs.rs/topcoat 0.9)**

`platform/apps/hub-web/Cargo.toml`:
```toml
[package]
name = "hub-web"
version = "0.1.0"
edition.workspace = true

[dependencies]
topcoat = "0.9"
tokio.workspace = true

[dev-dependencies]
reqwest.workspace = true
tokio.workspace = true
```

`platform/apps/hub-web/src/main.rs`:
```rust
use topcoat::{Result, router::{Router, RouterBuilderDiscoverExt, page}, view::{View, view}};

#[tokio::main]
async fn main() {
    topcoat::start(Router::builder().discover().build()).await.unwrap();
}

#[page("/")]
async fn home() -> Result<impl View> {
    let brand = std::env::var("HUB_BRAND_NAME").unwrap_or_else(|_| "Application Hub".into());
    Ok(view! {
        <!DOCTYPE html>
        <html lang="pt-BR"><body><h1>(brand)</h1></body></html>
    })
}
```

- [ ] **Step 4: Rodar e ver passar**

Run: `cd platform && cargo test -p hub-web`
Expected: PASS. Se a porta não vier de `PORT`, descobrir o mecanismo (config/env) e ajustar `spawn`.

- [ ] **Step 5: Registrar achados em `platform/docs/SPIKE-TOPCOAT.md`**

Responder, com trechos de código testados:
1. Variável/config de porta e bind.
2. Como declarar layout compartilhado (arquivo `_layout.rs`? componente com filhos?) e sintaxe de filhos em `#[component]`.
3. Como montar um serviço Axum/Tower (gateway) sob um prefixo no mesmo servidor, ou se exige processo separado.
4. Como servir arquivos estáticos (`asset!`) e CSS existente do mockup.
5. Tempo de `cargo build` limpo e incremental (medir no Windows e no CI).
6. Hot reload: comando (`topcoat start`?) e comportamento.

**Gate:** se o item 3 não for viável, o gateway roda como binário separado (`hub-gateway`) na frente do `hub-web`; atualizar Task 5 antes de seguir.

- [ ] **Step 6: Commit**

```bash
git add platform/apps/hub-web platform/docs/SPIKE-TOPCOAT.md
git commit -m "spike(rust-topcoat): shell mínimo e achados da API Topcoat 0.9"
```

---

### Task 2: `hub-manifest` — schema e validação

**Files:**
- Create: `platform/crates/hub-manifest/Cargo.toml`, `src/lib.rs`, `tests/validate.rs`

**Interfaces:**
- Produces:
  - `pub struct Manifest { id: String, name: String, version: semver::Version, mode: Mode, routes: Vec<String>, resources: Resources, env: Vec<String>, permissions: Vec<String> }`
  - `pub enum Mode { Fragment, WebComponent }` (serde `snake_case`: `fragment`, `web_component`)
  - `pub struct Resources { cpu: f64, memory: String, pids: u32 }` com `Default` (0.5, "512Mi", 100)
  - `pub fn parse(json: &str) -> Result<Manifest, ManifestError>` (desserializa + valida)
  - `pub fn memory_bytes(s: &str) -> Result<u64, ManifestError>` (`Ki|Mi|Gi`)
  - `pub enum ManifestError { Json(String), InvalidId(String), InvalidRoute(String), InvalidMemory(String), OutOfPolicy(String) }`

- [ ] **Step 1: Testes que falham**

`platform/crates/hub-manifest/tests/validate.rs`:
```rust
use hub_manifest::{parse, memory_bytes, ManifestError, Mode};

const OK: &str = r#"{"id":"fornecedores","name":"Rede de Fornecedores","version":"1.8.0",
  "mode":"fragment","routes":["/fornecedores/*"],
  "resources":{"cpu":0.5,"memory":"512Mi","pids":100}}"#;

#[test]
fn parses_valid_manifest() {
    let m = parse(OK).unwrap();
    assert_eq!(m.id, "fornecedores");
    assert_eq!(m.mode, Mode::Fragment);
    assert_eq!(m.resources.pids, 100);
}

#[test]
fn defaults_resources_when_missing() {
    let m = parse(r#"{"id":"abc","name":"A","version":"1.0.0","mode":"fragment","routes":["/abc/*"]}"#).unwrap();
    assert_eq!(m.resources.cpu, 0.5);
    assert_eq!(m.resources.memory, "512Mi");
}

#[test]
fn rejects_bad_id() {
    let e = parse(&OK.replace("\"fornecedores\",\"name\"", "\"Fornecedores!\",\"name\"")).unwrap_err();
    assert!(matches!(e, ManifestError::InvalidId(_)));
}

#[test]
fn rejects_route_outside_own_prefix() {
    let e = parse(&OK.replace("/fornecedores/*", "/team/*")).unwrap_err();
    assert!(matches!(e, ManifestError::InvalidRoute(_)));
}

#[test]
fn memory_units() {
    assert_eq!(memory_bytes("512Mi").unwrap(), 512 * 1024 * 1024);
    assert_eq!(memory_bytes("1Gi").unwrap(), 1024 * 1024 * 1024);
    assert!(matches!(memory_bytes("512MB"), Err(ManifestError::InvalidMemory(_))));
    assert!(matches!(memory_bytes("abc"), Err(ManifestError::InvalidMemory(_))));
}

#[test]
fn rejects_out_of_policy_resources() {
    for bad in [r#""cpu":0"#, r#""cpu":8"#] {
        let e = parse(&OK.replace(r#""cpu":0.5"#, bad)).unwrap_err();
        assert!(matches!(e, ManifestError::OutOfPolicy(_)));
    }
    let e = parse(&OK.replace("512Mi", "16Gi")).unwrap_err();
    assert!(matches!(e, ManifestError::OutOfPolicy(_)));
}

#[test]
fn malformed_json_is_error_not_panic() {
    assert!(matches!(parse("{"), Err(ManifestError::Json(_))));
}
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `cd platform && cargo test -p hub-manifest`
Expected: FAIL (crate inexistente).

- [ ] **Step 3: Implementação**

`platform/crates/hub-manifest/Cargo.toml`:
```toml
[package]
name = "hub-manifest"
version = "0.1.0"
edition.workspace = true

[dependencies]
serde.workspace = true
serde_json.workspace = true
semver.workspace = true
thiserror.workspace = true
```

`platform/crates/hub-manifest/src/lib.rs`:
```rust
use serde::{Deserialize, Serialize};
use thiserror::Error;

pub const MAX_CPU: f64 = 4.0;
pub const MAX_MEMORY: u64 = 8 * 1024 * 1024 * 1024;
pub const MAX_PIDS: u32 = 1024;

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Mode { Fragment, WebComponent }

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Resources {
    #[serde(default = "d_cpu")] pub cpu: f64,
    #[serde(default = "d_mem")] pub memory: String,
    #[serde(default = "d_pids")] pub pids: u32,
}
fn d_cpu() -> f64 { 0.5 }
fn d_mem() -> String { "512Mi".into() }
fn d_pids() -> u32 { 100 }
impl Default for Resources {
    fn default() -> Self { Self { cpu: d_cpu(), memory: d_mem(), pids: d_pids() } }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Manifest {
    pub id: String,
    pub name: String,
    pub version: semver::Version,
    pub mode: Mode,
    pub routes: Vec<String>,
    #[serde(default)] pub resources: Resources,
    #[serde(default)] pub env: Vec<String>,
    #[serde(default)] pub permissions: Vec<String>,
}

#[derive(Debug, Error, PartialEq)]
pub enum ManifestError {
    #[error("manifest inválido: {0}")] Json(String),
    #[error("id inválido: {0}")] InvalidId(String),
    #[error("rota inválida: {0}")] InvalidRoute(String),
    #[error("memória inválida: {0} (use Ki, Mi ou Gi)")] InvalidMemory(String),
    #[error("fora da política da plataforma: {0}")] OutOfPolicy(String),
}

pub fn memory_bytes(s: &str) -> Result<u64, ManifestError> {
    let err = || ManifestError::InvalidMemory(s.to_string());
    let (num, mul) = if let Some(n) = s.strip_suffix("Gi") { (n, 1u64 << 30) }
        else if let Some(n) = s.strip_suffix("Mi") { (n, 1u64 << 20) }
        else if let Some(n) = s.strip_suffix("Ki") { (n, 1u64 << 10) }
        else { return Err(err()) };
    let n: u64 = num.parse().map_err(|_| err())?;
    n.checked_mul(mul).filter(|v| *v > 0).ok_or_else(err)
}

fn valid_id(id: &str) -> bool {
    let mut c = id.chars();
    matches!(c.next(), Some('a'..='z'))
        && (2..=32).contains(&id.len())
        && id.chars().all(|ch| ch.is_ascii_lowercase() || ch.is_ascii_digit() || ch == '-')
}

pub fn validate(m: &Manifest) -> Result<(), ManifestError> {
    if !valid_id(&m.id) { return Err(ManifestError::InvalidId(m.id.clone())); }
    let prefix = format!("/{}/", m.id);
    if m.routes.is_empty() { return Err(ManifestError::InvalidRoute("nenhuma rota".into())); }
    for r in &m.routes {
        if !r.starts_with(&prefix) || r.contains("..") {
            return Err(ManifestError::InvalidRoute(r.clone()));
        }
    }
    let r = &m.resources;
    if !(r.cpu > 0.0 && r.cpu <= MAX_CPU) { return Err(ManifestError::OutOfPolicy(format!("cpu {}", r.cpu))); }
    if memory_bytes(&r.memory)? > MAX_MEMORY { return Err(ManifestError::OutOfPolicy(format!("memory {}", r.memory))); }
    if r.pids == 0 || r.pids > MAX_PIDS { return Err(ManifestError::OutOfPolicy(format!("pids {}", r.pids))); }
    Ok(())
}

pub fn parse(json: &str) -> Result<Manifest, ManifestError> {
    let m: Manifest = serde_json::from_str(json).map_err(|e| ManifestError::Json(e.to_string()))?;
    validate(&m)?;
    Ok(m)
}
```

- [ ] **Step 4: Rodar e ver passar**

Run: `cd platform && cargo test -p hub-manifest`
Expected: PASS (7 testes).

- [ ] **Step 5: Commit**

```bash
git add platform/crates/hub-manifest
git commit -m "feat(rust-topcoat): hub-manifest com validação e política de recursos"
```

---

### Task 3: `hub-registry` — registro e resolução de rotas

**Files:**
- Create: `platform/crates/hub-registry/Cargo.toml`, `src/lib.rs`, `tests/registry.rs`

**Interfaces:**
- Consumes: `hub_manifest::Manifest`
- Produces:
  - `pub struct AppEntry { pub manifest: Manifest, pub upstream: String }`
  - `pub struct Registry` com `new()`, `register(AppEntry) -> Result<(), RegistryError>`, `list() -> Vec<&AppEntry>` (ordenado por `id`), `resolve(path: &str) -> Option<&AppEntry>`
  - `pub enum RegistryError { DuplicateId(String), RouteConflict(String, String) }`

- [ ] **Step 1: Testes que falham**

`platform/crates/hub-registry/tests/registry.rs`:
```rust
use hub_manifest::parse;
use hub_registry::{AppEntry, Registry, RegistryError};

fn entry(id: &str, routes: &[&str]) -> AppEntry {
    let routes = routes.iter().map(|r| format!("\"{r}\"")).collect::<Vec<_>>().join(",");
    let json = format!(r#"{{"id":"{id}","name":"{id}","version":"1.0.0","mode":"fragment","routes":[{routes}]}}"#);
    AppEntry { manifest: parse(&json).unwrap(), upstream: format!("http://{id}:8080") }
}

#[test]
fn resolves_by_prefix() {
    let mut r = Registry::new();
    r.register(entry("ativos", &["/ativos/*"])).unwrap();
    assert_eq!(r.resolve("/ativos/assets").unwrap().manifest.id, "ativos");
    assert!(r.resolve("/team/admin").is_none());
    assert!(r.resolve("/ativos").is_some());
}

#[test]
fn rejects_duplicate_id() {
    let mut r = Registry::new();
    r.register(entry("ativos", &["/ativos/*"])).unwrap();
    assert!(matches!(r.register(entry("ativos", &["/ativos/*"])), Err(RegistryError::DuplicateId(_))));
}

#[test]
fn rejects_path_traversal_on_resolve() {
    let mut r = Registry::new();
    r.register(entry("ativos", &["/ativos/*"])).unwrap();
    assert!(r.resolve("/ativos/../team/admin").is_none());
    assert!(r.resolve("/ativos/%2e%2e/team").is_none());
}

#[test]
fn list_is_sorted() {
    let mut r = Registry::new();
    r.register(entry("zeta", &["/zeta/*"])).unwrap();
    r.register(entry("alfa", &["/alfa/*"])).unwrap();
    let ids: Vec<_> = r.list().iter().map(|e| e.manifest.id.clone()).collect();
    assert_eq!(ids, ["alfa", "zeta"]);
}
```

Nota: como o manifest já exige rotas sob `/<id>/`, conflito de rotas entre apps distintos só ocorre via id duplicado; `RouteConflict` protege rotas registradas diretamente por plugins futuros e é coberto pela checagem de prefixo abaixo.

- [ ] **Step 2: Rodar e ver falhar**

Run: `cd platform && cargo test -p hub-registry`
Expected: FAIL.

- [ ] **Step 3: Implementação**

`platform/crates/hub-registry/Cargo.toml`:
```toml
[package]
name = "hub-registry"
version = "0.1.0"
edition.workspace = true

[dependencies]
hub-manifest = { path = "../hub-manifest" }
thiserror.workspace = true
```

`platform/crates/hub-registry/src/lib.rs`:
```rust
use hub_manifest::Manifest;
use std::collections::BTreeMap;
use thiserror::Error;

#[derive(Debug, Clone)]
pub struct AppEntry { pub manifest: Manifest, pub upstream: String }

#[derive(Debug, Error, PartialEq)]
pub enum RegistryError {
    #[error("aplicação já registrada: {0}")] DuplicateId(String),
    #[error("rota {0} conflita com {1}")] RouteConflict(String, String),
}

#[derive(Default)]
pub struct Registry { apps: BTreeMap<String, AppEntry> }

fn prefix(route: &str) -> &str { route.trim_end_matches('*') }

impl Registry {
    pub fn new() -> Self { Self::default() }

    pub fn register(&mut self, e: AppEntry) -> Result<(), RegistryError> {
        let id = e.manifest.id.clone();
        if self.apps.contains_key(&id) { return Err(RegistryError::DuplicateId(id)); }
        for other in self.apps.values() {
            for a in &e.manifest.routes {
                for b in &other.manifest.routes {
                    if prefix(a).starts_with(prefix(b)) || prefix(b).starts_with(prefix(a)) {
                        return Err(RegistryError::RouteConflict(a.clone(), b.clone()));
                    }
                }
            }
        }
        self.apps.insert(id, e);
        Ok(())
    }

    pub fn list(&self) -> Vec<&AppEntry> { self.apps.values().collect() }

    pub fn resolve(&self, path: &str) -> Option<&AppEntry> {
        let lower = path.to_ascii_lowercase();
        if lower.contains("..") || lower.contains("%2e") { return None; }
        let id = path.trim_start_matches('/').split('/').next()?;
        let e = self.apps.get(id)?;
        let with_slash = if path.ends_with('/') { path.to_string() } else { format!("{path}/") };
        e.manifest.routes.iter().any(|r| with_slash.starts_with(prefix(r))).then_some(e)
    }
}
```

- [ ] **Step 4: Rodar e ver passar**

Run: `cd platform && cargo test -p hub-registry`
Expected: PASS (4 testes).

- [ ] **Step 5: Commit**

```bash
git add platform/crates/hub-registry
git commit -m "feat(rust-topcoat): hub-registry com resolução segura de rotas"
```

---

### Task 4: Shell SSR em Topcoat (header, rail, catálogo)

**Files:**
- Modify: `platform/apps/hub-web/Cargo.toml`, `platform/apps/hub-web/src/main.rs`
- Create: `platform/apps/hub-web/src/shell.rs`, `platform/apps/hub-web/src/apps.rs`, `platform/apps/hub-web/apps.json`, `platform/apps/hub-web/tests/shell.rs`

**Interfaces:**
- Consumes: `hub_registry::{Registry, AppEntry}`, `hub_manifest::parse`; achados do SPIKE (layout e filhos).
- Produces: `pub fn load_registry(path: &str) -> anyhow::Result<Registry>` (lê `apps.json`: array de `{ "manifest": {...}, "upstream": "..." }`); páginas `GET /` e `GET /applications` dentro do shell.

- [ ] **Step 1: Teste que falha**

`platform/apps/hub-web/tests/shell.rs`:
```rust
mod smoke;
use smoke::spawn;

#[tokio::test]
async fn applications_lists_registered_apps_inside_shell() {
    let _s = spawn(18081).await;
    let body = reqwest::get("http://127.0.0.1:18081/applications").await.unwrap().text().await.unwrap();
    assert!(body.contains("class=\"platform-header\""), "header da plataforma sempre presente");
    assert!(body.contains("class=\"platform-rail\""));
    assert!(body.contains("Gestão de Ativos"));
    assert!(body.contains("href=\"/ativos/\""));
}
```
(Mover `spawn`/`Server` de `tests/smoke.rs` para `tests/smoke/mod.rs` e manter `home_renders_brand` em `tests/smoke_home.rs` usando o mesmo módulo.)

- [ ] **Step 2: Rodar e ver falhar**

Run: `cd platform && cargo test -p hub-web --test shell`
Expected: FAIL (404 ou sem header).

- [ ] **Step 3: Implementação**

`platform/apps/hub-web/apps.json`:
```json
[
  { "manifest": { "id": "ativos", "name": "Gestão de Ativos", "version": "2.4.1", "mode": "fragment", "routes": ["/ativos/*"] }, "upstream": "http://127.0.0.1:9101" },
  { "manifest": { "id": "fornecedores", "name": "Rede de Fornecedores", "version": "1.8.0", "mode": "fragment", "routes": ["/fornecedores/*"] }, "upstream": "http://127.0.0.1:9102" },
  { "manifest": { "id": "servicos", "name": "Central de Serviços", "version": "3.1.0", "mode": "web_component", "routes": ["/servicos/*"] }, "upstream": "http://127.0.0.1:9103" }
]
```

Adicionar em `platform/apps/hub-web/Cargo.toml`:
```toml
hub-manifest = { path = "../../crates/hub-manifest" }
hub-registry = { path = "../../crates/hub-registry" }
serde.workspace = true
serde_json.workspace = true
anyhow.workspace = true
```

`platform/apps/hub-web/src/apps.rs`:
```rust
use hub_registry::{AppEntry, Registry};
use serde::Deserialize;
use std::sync::OnceLock;

#[derive(Deserialize)]
struct Raw { manifest: serde_json::Value, upstream: String }

pub fn load_registry(path: &str) -> anyhow::Result<Registry> {
    let raw: Vec<Raw> = serde_json::from_str(&std::fs::read_to_string(path)?)?;
    let mut reg = Registry::new();
    for r in raw {
        let manifest = hub_manifest::parse(&r.manifest.to_string())?;
        reg.register(AppEntry { manifest, upstream: r.upstream })?;
    }
    Ok(reg)
}

static REGISTRY: OnceLock<Registry> = OnceLock::new();

pub fn registry() -> &'static Registry {
    REGISTRY.get_or_init(|| {
        let path = std::env::var("HUB_APPS_FILE")
            .unwrap_or_else(|_| concat!(env!("CARGO_MANIFEST_DIR"), "/apps.json").into());
        load_registry(&path).expect("apps.json válido")
    })
}
```

`platform/apps/hub-web/src/shell.rs` (sintaxe de filhos/layout conforme SPIKE item 2; abaixo, variante com `content` como parâmetro):
```rust
use topcoat::{Result, view::{View, component, view}};

pub fn brand() -> String {
    std::env::var("HUB_BRAND_NAME").unwrap_or_else(|_| "Application Hub".into())
}

#[component]
pub async fn shell(title: &str, content: impl View) -> Result<impl View> {
    let brand = brand();
    Ok(view! {
        <!DOCTYPE html>
        <html lang="pt-BR">
          <head><meta charset="utf-8"/><title>(title) " · " (brand)</title></head>
          <body>
            <header class="platform-header"><a class="brand" href="/">(brand)</a></header>
            <aside class="platform-rail">
              <a href="/">"Início"</a>
              <a href="/applications">"Aplicações"</a>
            </aside>
            <main id="app-root">(content)</main>
          </body>
        </html>
    })
}
```

`platform/apps/hub-web/src/main.rs`:
```rust
mod apps;
mod shell;

use topcoat::{Result, router::{Router, RouterBuilderDiscoverExt, page}, view::{View, view}};
use shell::shell;

#[tokio::main]
async fn main() {
    apps::registry();
    topcoat::start(Router::builder().discover().build()).await.unwrap();
}

#[page("/")]
async fn home() -> Result<impl View> {
    Ok(view! { shell(title: "Início", content: view! { <h1>(shell::brand())</h1> }) })
}

#[page("/applications")]
async fn applications() -> Result<impl View> {
    let apps = apps::registry().list();
    Ok(view! {
        shell(title: "Aplicações", content: view! {
            <h1>"Aplicações"</h1>
            <ul>
              for e in apps {
                <li><a href=(format!("/{}/", e.manifest.id))>(e.manifest.name.clone())</a></li>
              }
            </ul>
        })
    })
}
```

- [ ] **Step 4: Rodar e ver passar**

Run: `cd platform && cargo test -p hub-web`
Expected: PASS (smoke + shell).

- [ ] **Step 5: Portar CSS do mockup**

Copiar `css/styles.css` da `main` para `platform/apps/hub-web/assets/styles.css`, servir via `asset!` (SPIKE item 4) e referenciar no `<head>` do `shell`. Verificar visualmente `http://localhost:<porta>/applications` contra o mockup.

- [ ] **Step 6: Commit**

```bash
git add platform/apps/hub-web
git commit -m "feat(rust-topcoat): shell SSR com header, rail e catálogo de apps"
```

---

### Task 5: `hub-gateway` — proxy com identidade

**Files:**
- Create: `platform/crates/hub-gateway/Cargo.toml`, `src/lib.rs`, `tests/proxy.rs`

**Interfaces:**
- Consumes: `hub_registry::Registry`
- Produces:
  - `pub struct Identity { pub user: String, pub roles: Vec<String> }` (serde)
  - `pub fn router(reg: Arc<Registry>, identity: fn(&HeaderMap) -> Identity) -> axum::Router`
  - Header de saída `x-hub-user` = base64url(JSON de `Identity`); `x-hub-*` do cliente removidos.
  - Upstream indisponível → `502` com corpo HTML contendo `Aplicação indisponível`.

- [ ] **Step 1: Testes que falham**

`platform/crates/hub-gateway/tests/proxy.rs`:
```rust
use axum::{Router, routing::any, http::HeaderMap};
use hub_gateway::{router, Identity};
use hub_registry::{AppEntry, Registry};
use std::sync::Arc;
use tokio::net::TcpListener;

async fn serve(app: Router) -> String {
    let l = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = l.local_addr().unwrap();
    tokio::spawn(async move { axum::serve(l, app).await.unwrap() });
    format!("http://{addr}")
}

fn dev_identity(_: &HeaderMap) -> Identity { Identity { user: "john.doe".into(), roles: vec!["admin".into()] } }

fn registry(upstream: &str) -> Arc<Registry> {
    let m = hub_manifest::parse(r#"{"id":"ativos","name":"Ativos","version":"1.0.0","mode":"fragment","routes":["/ativos/*"]}"#).unwrap();
    let mut r = Registry::new();
    r.register(AppEntry { manifest: m, upstream: upstream.into() }).unwrap();
    Arc::new(r)
}

#[tokio::test]
async fn forwards_path_and_injects_identity_dropping_forged_header() {
    let upstream = serve(Router::new().route("/{*p}", any(|h: HeaderMap, uri: axum::http::Uri| async move {
        format!("{}|{}", uri.path(), h.get("x-hub-user").unwrap().to_str().unwrap())
    }))).await;
    let hub = serve(router(registry(&upstream), dev_identity)).await;
    let body = reqwest::Client::new().get(format!("{hub}/ativos/assets"))
        .header("x-hub-user", "forjado").send().await.unwrap().text().await.unwrap();
    let (path, ident) = body.split_once('|').unwrap();
    assert_eq!(path, "/ativos/assets");
    assert_ne!(ident, "forjado");
    let json = base64::Engine::decode(&base64::engine::general_purpose::URL_SAFE_NO_PAD, ident).unwrap();
    let id: Identity = serde_json::from_slice(&json).unwrap();
    assert_eq!(id.user, "john.doe");
}

#[tokio::test]
async fn upstream_down_returns_502() {
    let hub = serve(router(registry("http://127.0.0.1:1"), dev_identity)).await;
    let r = reqwest::get(format!("{hub}/ativos/")).await.unwrap();
    assert_eq!(r.status(), 502);
    assert!(r.text().await.unwrap().contains("Aplicação indisponível"));
}

#[tokio::test]
async fn traversal_and_unknown_app_are_404() {
    let hub = serve(router(registry("http://127.0.0.1:1"), dev_identity)).await;
    for p in ["/ativos/../team/admin", "/ativos/%2e%2e/team", "/desconhecido/x"] {
        assert_eq!(reqwest::get(format!("{hub}{p}")).await.unwrap().status(), 404, "{p}");
    }
}
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `cd platform && cargo test -p hub-gateway`
Expected: FAIL.

- [ ] **Step 3: Implementação**

`platform/crates/hub-gateway/Cargo.toml`:
```toml
[package]
name = "hub-gateway"
version = "0.1.0"
edition.workspace = true

[dependencies]
hub-registry = { path = "../hub-registry" }
axum.workspace = true
reqwest.workspace = true
serde.workspace = true
serde_json.workspace = true
base64.workspace = true
tokio.workspace = true

[dev-dependencies]
hub-manifest = { path = "../hub-manifest" }
```

`platform/crates/hub-gateway/src/lib.rs`:
```rust
use axum::{
    body::{Body, to_bytes},
    extract::{Request, State},
    http::{HeaderMap, HeaderName, HeaderValue, StatusCode},
    response::{Html, IntoResponse, Response},
    Router,
};
use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine};
use hub_registry::Registry;
use serde::{Deserialize, Serialize};
use std::sync::Arc;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Identity { pub user: String, pub roles: Vec<String> }

#[derive(Clone)]
struct Gw { reg: Arc<Registry>, identity: fn(&HeaderMap) -> Identity, http: reqwest::Client }

pub fn router(reg: Arc<Registry>, identity: fn(&HeaderMap) -> Identity) -> Router {
    Router::new().fallback(forward).with_state(Gw { reg, identity, http: reqwest::Client::new() })
}

const MAX_BODY: usize = 16 * 1024 * 1024;

async fn forward(State(gw): State<Gw>, req: Request) -> Response {
    let path_q = req.uri().path_and_query().map(|p| p.as_str().to_string()).unwrap_or_default();
    let Some(app) = gw.reg.resolve(req.uri().path()) else {
        return StatusCode::NOT_FOUND.into_response();
    };
    let ident = URL_SAFE_NO_PAD.encode(serde_json::to_vec(&(gw.identity)(req.headers())).unwrap());
    let (parts, body) = req.into_parts();
    let Ok(bytes) = to_bytes(body, MAX_BODY).await else { return StatusCode::PAYLOAD_TOO_LARGE.into_response() };

    let mut headers = reqwest::header::HeaderMap::new();
    for (k, v) in parts.headers.iter() {
        if k.as_str().starts_with("x-hub-") || k == "host" { continue; }
        headers.insert(k.clone(), v.clone());
    }
    headers.insert(HeaderName::from_static("x-hub-user"), HeaderValue::from_str(&ident).unwrap());

    let url = format!("{}{}", app.upstream.trim_end_matches('/'), path_q);
    match gw.http.request(parts.method, url).headers(headers).body(bytes).send().await {
        Ok(r) => {
            let status = r.status();
            let hs = r.headers().clone();
            let body = r.bytes().await.unwrap_or_default();
            let mut resp = Response::new(Body::from(body));
            *resp.status_mut() = status;
            *resp.headers_mut() = hs;
            resp
        }
        Err(_) => (StatusCode::BAD_GATEWAY, Html(format!(
            "<section class=\"empty-state\"><h1>Aplicação indisponível</h1><p>{} não respondeu. Tente novamente em instantes.</p></section>",
            app.manifest.name))).into_response(),
    }
}
```

- [ ] **Step 4: Rodar e ver passar**

Run: `cd platform && cargo test -p hub-gateway`
Expected: PASS (3 testes).

- [ ] **Step 5: Montar no hub-web** conforme SPIKE item 3 (mesmo servidor sob fallback de `/<id>/*`, ou binário `hub-gateway` na frente). Teste manual: subir um upstream (`python -m http.server 9101`) e abrir `/ativos/`.

- [ ] **Step 6: Commit**

```bash
git add platform/crates/hub-gateway platform/apps/hub-web
git commit -m "feat(rust-topcoat): gateway com identidade injetada e 502 amigável"
```

---

### Task 6: `hub-runtime` — containers Docker/Podman com limites

**Files:**
- Create: `platform/crates/hub-runtime/Cargo.toml`, `src/lib.rs`, `tests/spec.rs`

**Interfaces:**
- Consumes: `hub_manifest::{Manifest, memory_bytes}`
- Produces:
  - `pub struct ContainerSpec { pub name: String, pub image: String, pub nano_cpus: i64, pub memory: i64, pub pids: i64, pub network: String }`
  - `pub fn spec_for(m: &Manifest, image: &str) -> ContainerSpec`
  - `pub fn host_config(s: &ContainerSpec) -> bollard::models::HostConfig`
  - `#[async_trait] pub trait ContainerRuntime { async fn run(&self, s: &ContainerSpec) -> anyhow::Result<String>; }`
  - `pub struct EngineRuntime` (bollard, socket de `HUB_CONTAINER_SOCKET`)

- [ ] **Step 1: Testes que falham** (puros, sem daemon)

`platform/crates/hub-runtime/tests/spec.rs`:
```rust
use hub_runtime::{host_config, spec_for};

#[test]
fn spec_applies_manifest_limits_and_hardening() {
    let m = hub_manifest::parse(r#"{"id":"ativos","name":"A","version":"1.0.0","mode":"fragment",
      "routes":["/ativos/*"],"resources":{"cpu":0.5,"memory":"512Mi","pids":100}}"#).unwrap();
    let s = spec_for(&m, "ghcr.io/org/ativos@sha256:abc");
    assert_eq!(s.name, "hub-app-ativos");
    assert_eq!(s.nano_cpus, 500_000_000);
    assert_eq!(s.memory, 512 * 1024 * 1024);
    let h = host_config(&s);
    assert_eq!(h.memory_swap, Some(s.memory), "sem swap extra");
    assert_eq!(h.pids_limit, Some(100));
    assert_eq!(h.readonly_rootfs, Some(true));
    assert_eq!(h.cap_drop, Some(vec!["ALL".to_string()]));
    assert_eq!(h.security_opt, Some(vec!["no-new-privileges".to_string()]));
    assert_eq!(h.network_mode.as_deref(), Some("hub-app-ativos"));
}
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `cd platform && cargo test -p hub-runtime`
Expected: FAIL.

- [ ] **Step 3: Implementação**

`platform/crates/hub-runtime/Cargo.toml`:
```toml
[package]
name = "hub-runtime"
version = "0.1.0"
edition.workspace = true

[dependencies]
hub-manifest = { path = "../hub-manifest" }
bollard.workspace = true
anyhow.workspace = true
async-trait = "0.1"
```

`platform/crates/hub-runtime/src/lib.rs`:
```rust
use bollard::{container::{Config, CreateContainerOptions, StartContainerOptions}, models::{HostConfig, RestartPolicy, RestartPolicyNameEnum}, Docker};
use hub_manifest::{memory_bytes, Manifest};

#[derive(Debug, Clone, PartialEq)]
pub struct ContainerSpec { pub name: String, pub image: String, pub nano_cpus: i64, pub memory: i64, pub pids: i64, pub network: String }

pub fn spec_for(m: &Manifest, image: &str) -> ContainerSpec {
    let name = format!("hub-app-{}", m.id);
    ContainerSpec {
        network: name.clone(),
        name,
        image: image.to_string(),
        nano_cpus: (m.resources.cpu * 1e9) as i64,
        memory: memory_bytes(&m.resources.memory).expect("manifest já validado") as i64,
        pids: m.resources.pids as i64,
    }
}

pub fn host_config(s: &ContainerSpec) -> HostConfig {
    HostConfig {
        nano_cpus: Some(s.nano_cpus),
        memory: Some(s.memory),
        memory_swap: Some(s.memory),
        pids_limit: Some(s.pids),
        readonly_rootfs: Some(true),
        cap_drop: Some(vec!["ALL".into()]),
        security_opt: Some(vec!["no-new-privileges".into()]),
        network_mode: Some(s.network.clone()),
        restart_policy: Some(RestartPolicy { name: Some(RestartPolicyNameEnum::UNLESS_STOPPED), maximum_retry_count: None }),
        ..Default::default()
    }
}

#[async_trait::async_trait]
pub trait ContainerRuntime { async fn run(&self, s: &ContainerSpec) -> anyhow::Result<String>; }

pub struct EngineRuntime { docker: Docker }

impl EngineRuntime {
    /// Docker (`/var/run/docker.sock`) ou Podman (`/run/podman/podman.sock`) via HUB_CONTAINER_SOCKET.
    pub fn from_env() -> anyhow::Result<Self> {
        let docker = match std::env::var("HUB_CONTAINER_SOCKET") {
            Ok(sock) => Docker::connect_with_socket(&sock, 120, bollard::API_DEFAULT_VERSION)?,
            Err(_) => Docker::connect_with_local_defaults()?,
        };
        Ok(Self { docker })
    }
}

#[async_trait::async_trait]
impl ContainerRuntime for EngineRuntime {
    async fn run(&self, s: &ContainerSpec) -> anyhow::Result<String> {
        let cfg = Config { image: Some(s.image.clone()), host_config: Some(host_config(s)), ..Default::default() };
        let created = self.docker.create_container(Some(CreateContainerOptions { name: s.name.clone(), platform: None }), cfg).await?;
        self.docker.start_container(&created.id, None::<StartContainerOptions<String>>).await?;
        Ok(created.id)
    }
}
```

- [ ] **Step 4: Rodar e ver passar**

Run: `cd platform && cargo test -p hub-runtime`
Expected: PASS. Se a versão do bollard mudar nomes (`Config`/`CreateContainerOptions`), ajustar ao docs.rs da versão travada no `Cargo.lock`.

- [ ] **Step 5: Teste manual com Docker e com Podman**

Criar a rede (`docker network create hub-app-ativos` / `podman network create hub-app-ativos`), rodar um exemplo `cargo run -p hub-runtime --example run -- nginx:alpine` e conferir `docker inspect hub-app-ativos --format '{{.HostConfig.NanoCpus}} {{.HostConfig.Memory}} {{.HostConfig.PidsLimit}}'`. Repetir com `HUB_CONTAINER_SOCKET` apontando para o socket do Podman. Criar `examples/run.rs` com:
```rust
use hub_runtime::{spec_for, ContainerRuntime, EngineRuntime};
#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let image = std::env::args().nth(1).unwrap_or_else(|| "nginx:alpine".into());
    let m = hub_manifest::parse(r#"{"id":"ativos","name":"A","version":"1.0.0","mode":"fragment","routes":["/ativos/*"]}"#)?;
    println!("{}", EngineRuntime::from_env()?.run(&spec_for(&m, &image)).await?);
    Ok(())
}
```
(adicionar `tokio.workspace = true` em `[dev-dependencies]`).

- [ ] **Step 6: Commit**

```bash
git add platform/crates/hub-runtime
git commit -m "feat(rust-topcoat): hub-runtime com limites e hardening (Docker/Podman)"
```

---

### Task 7: `hub pack` — gerar `.hubapp`

**Files:**
- Create: `platform/crates/hub-cli/Cargo.toml`, `src/main.rs`, `src/pack.rs`, `tests/pack.rs`

**Interfaces:**
- Consumes: `hub_manifest::parse`
- Produces: `pub fn pack(dir: &Path, image: &str, out: &Path) -> anyhow::Result<String>` → retorna sha256 hex do pacote. Conteúdo: `manifest.json`, `runtime.json` (`{"image": "<ref@digest>"}`), `ui/**` (se existir), `checksums.txt` (`<sha256>  <caminho>` por arquivo). Assinatura (cosign) fica para a Fase 2.

- [ ] **Step 1: Teste que falha**

`platform/crates/hub-cli/tests/pack.rs`:
```rust
use std::{fs, io::Read};

#[test]
fn pack_contains_manifest_runtime_and_checksums() {
    let dir = tempfile::tempdir().unwrap();
    fs::write(dir.path().join("manifest.json"), r#"{"id":"ativos","name":"A","version":"1.0.0","mode":"fragment","routes":["/ativos/*"]}"#).unwrap();
    fs::create_dir(dir.path().join("ui")).unwrap();
    fs::write(dir.path().join("ui/app.js"), "export {}").unwrap();
    let out = dir.path().join("ativos.hubapp");
    let sha = hub_cli::pack::pack(dir.path(), "ghcr.io/org/ativos@sha256:abc", &out).unwrap();
    assert_eq!(sha.len(), 64);
    let mut z = zip::ZipArchive::new(fs::File::open(&out).unwrap()).unwrap();
    for f in ["manifest.json", "runtime.json", "ui/app.js", "checksums.txt"] { z.by_name(f).unwrap(); }
    let mut sums = String::new();
    z.by_name("checksums.txt").unwrap().read_to_string(&mut sums).unwrap();
    assert!(sums.contains("  ui/app.js"));
}

#[test]
fn pack_refuses_invalid_manifest() {
    let dir = tempfile::tempdir().unwrap();
    fs::write(dir.path().join("manifest.json"), r#"{"id":"X","name":"A","version":"1.0.0","mode":"fragment","routes":["/X/*"]}"#).unwrap();
    assert!(hub_cli::pack::pack(dir.path(), "img", &dir.path().join("o.hubapp")).is_err());
}

#[test]
fn pack_refuses_image_without_digest() {
    let dir = tempfile::tempdir().unwrap();
    fs::write(dir.path().join("manifest.json"), r#"{"id":"ativos","name":"A","version":"1.0.0","mode":"fragment","routes":["/ativos/*"]}"#).unwrap();
    assert!(hub_cli::pack::pack(dir.path(), "ghcr.io/org/ativos:latest", &dir.path().join("o.hubapp")).is_err());
}
```
(No segundo teste o id `X` é inválido; o erro vem da validação do manifest antes da checagem de digest.)

- [ ] **Step 2: Rodar e ver falhar**

Run: `cd platform && cargo test -p hub-cli`
Expected: FAIL.

- [ ] **Step 3: Implementação**

`platform/crates/hub-cli/Cargo.toml`:
```toml
[package]
name = "hub-cli"
version = "0.1.0"
edition.workspace = true

[lib]
path = "src/lib.rs"

[[bin]]
name = "hub"
path = "src/main.rs"

[dependencies]
hub-manifest = { path = "../hub-manifest" }
hub-registry = { path = "../hub-registry" }
hub-gateway = { path = "../hub-gateway" }
anyhow.workspace = true
clap.workspace = true
serde_json.workspace = true
sha2.workspace = true
zip.workspace = true
tokio.workspace = true
axum.workspace = true

[dev-dependencies]
tempfile = "3"
zip.workspace = true
```

`platform/crates/hub-cli/src/lib.rs`:
```rust
pub mod pack;
pub mod dev;
```

`platform/crates/hub-cli/src/pack.rs`:
```rust
use anyhow::{bail, Context, Result};
use sha2::{Digest, Sha256};
use std::{fs, io::Write, path::{Path, PathBuf}};

fn hex(b: &[u8]) -> String { b.iter().map(|x| format!("{x:02x}")).collect() }

fn files_under(root: &Path, rel: &Path, out: &mut Vec<PathBuf>) -> Result<()> {
    let dir = root.join(rel);
    if !dir.exists() { return Ok(()); }
    for e in fs::read_dir(dir)? {
        let e = e?;
        let r = rel.join(e.file_name());
        if e.file_type()?.is_dir() { files_under(root, &r, out)? } else { out.push(r) }
    }
    Ok(())
}

pub fn pack(dir: &Path, image: &str, out: &Path) -> Result<String> {
    let manifest = fs::read_to_string(dir.join("manifest.json")).context("manifest.json não encontrado")?;
    hub_manifest::parse(&manifest)?;
    if !image.contains("@sha256:") { bail!("imagem deve ser referenciada por digest (…@sha256:…)"); }

    let mut entries: Vec<(String, Vec<u8>)> = vec![
        ("manifest.json".into(), manifest.into_bytes()),
        ("runtime.json".into(), serde_json::to_vec_pretty(&serde_json::json!({ "image": image }))?),
    ];
    let mut ui = Vec::new();
    files_under(dir, Path::new("ui"), &mut ui)?;
    ui.sort();
    for p in ui {
        entries.push((p.to_string_lossy().replace('\\', "/"), fs::read(dir.join(&p))?));
    }
    let sums: String = entries.iter().map(|(n, b)| format!("{}  {}\n", hex(&Sha256::digest(b)), n)).collect();
    entries.push(("checksums.txt".into(), sums.into_bytes()));

    let mut z = zip::ZipWriter::new(fs::File::create(out)?);
    let opts = zip::write::SimpleFileOptions::default();
    for (name, bytes) in &entries { z.start_file(name, opts)?; z.write_all(bytes)?; }
    z.finish()?;
    Ok(hex(&Sha256::digest(fs::read(out)?)))
}
```

`platform/crates/hub-cli/src/main.rs`:
```rust
use clap::{Parser, Subcommand};
use std::path::PathBuf;

#[derive(Parser)]
#[command(name = "hub", about = "CLI da Application Hub")]
struct Cli { #[command(subcommand)] cmd: Cmd }

#[derive(Subcommand)]
enum Cmd {
    /// Gera o pacote .hubapp a partir da pasta do app
    Pack { #[arg(long, default_value = ".")] dir: PathBuf, #[arg(long)] image: String, #[arg(long)] out: Option<PathBuf> },
    /// Shell local + proxy para o app rodando nativo
    Dev { #[arg(long, default_value = ".")] dir: PathBuf, #[arg(long)] upstream: String, #[arg(long, default_value_t = 4173)] port: u16, #[arg(long, default_value = "admin")] role: String },
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    match Cli::parse().cmd {
        Cmd::Pack { dir, image, out } => {
            let m = hub_manifest::parse(&std::fs::read_to_string(dir.join("manifest.json"))?)?;
            let out = out.unwrap_or_else(|| PathBuf::from(format!("{}-{}.hubapp", m.id, m.version)));
            println!("{}  {}", hub_cli::pack::pack(&dir, &image, &out)?, out.display());
        }
        Cmd::Dev { dir, upstream, port, role } => hub_cli::dev::run(&dir, &upstream, port, &role).await?,
    }
    Ok(())
}
```

- [ ] **Step 4: Rodar e ver passar**

Run: `cd platform && cargo test -p hub-cli --test pack`
Expected: PASS (3 testes). (`dev` é criado na Task 8; até lá, criar `src/dev.rs` com `pub async fn run(_: &std::path::Path, _: &str, _: u16, _: &str) -> anyhow::Result<()> { anyhow::bail!("não implementado") }` para compilar.)

- [ ] **Step 5: Commit**

```bash
git add platform/crates/hub-cli
git commit -m "feat(rust-topcoat): hub pack gera .hubapp com checksums e imagem por digest"
```

---

### Task 8: `hub dev` — desenvolvimento local sem Docker

**Files:**
- Modify: `platform/crates/hub-cli/src/dev.rs`
- Create: `platform/crates/hub-cli/tests/dev.rs`

**Interfaces:**
- Consumes: `hub_manifest::parse`, `hub_registry::{Registry, AppEntry}`, `hub_gateway::{router, Identity}`
- Produces:
  - `pub fn dev_router(dir: &Path, upstream: &str, role: &str) -> anyhow::Result<axum::Router>`
  - `pub async fn run(dir: &Path, upstream: &str, port: u16, role: &str) -> anyhow::Result<()>`
  - Variáveis de `.hub/dev.env` (formato `CHAVE=valor`) repassadas ao upstream como `x-hub-env-<chave-minúscula>` **somente** se declaradas em `manifest.env`.

- [ ] **Step 1: Teste que falha**

`platform/crates/hub-cli/tests/dev.rs`:
```rust
use axum::{routing::any, http::HeaderMap, Router};
use std::fs;
use tokio::net::TcpListener;

async fn serve(app: Router) -> String {
    let l = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let a = l.local_addr().unwrap();
    tokio::spawn(async move { axum::serve(l, app).await.unwrap() });
    format!("http://{a}")
}

#[tokio::test]
async fn dev_proxies_with_role_and_only_declared_env() {
    let upstream = serve(Router::new().route("/{*p}", any(|h: HeaderMap| async move {
        format!("{}|{}", h.get("x-hub-env-api_base_url").map(|v| v.to_str().unwrap()).unwrap_or("-"),
                         h.get("x-hub-env-nao_declarada").map(|_| "vazou").unwrap_or("ok"))
    }))).await;
    let dir = tempfile::tempdir().unwrap();
    fs::write(dir.path().join("manifest.json"), r#"{"id":"ativos","name":"A","version":"1.0.0","mode":"fragment","routes":["/ativos/*"],"env":["API_BASE_URL"]}"#).unwrap();
    fs::create_dir(dir.path().join(".hub")).unwrap();
    fs::write(dir.path().join(".hub/dev.env"), "API_BASE_URL=http://localhost:9000\nNAO_DECLARADA=x\n").unwrap();
    let hub = serve(hub_cli::dev::dev_router(dir.path(), &upstream, "admin").unwrap()).await;
    let body = reqwest::get(format!("{hub}/ativos/")).await.unwrap().text().await.unwrap();
    assert_eq!(body, "http://localhost:9000|ok");
}
```
(Adicionar `reqwest.workspace = true` em `[dev-dependencies]` do `hub-cli`.)

- [ ] **Step 2: Rodar e ver falhar**

Run: `cd platform && cargo test -p hub-cli --test dev`
Expected: FAIL ("não implementado").

- [ ] **Step 3: Implementação**

Estender `hub-gateway` com variante que aceita cabeçalhos extras — em `platform/crates/hub-gateway/src/lib.rs` adicionar campo `extra: Arc<Vec<(HeaderName, HeaderValue)>>` em `Gw`, função pública:
```rust
pub fn router_with_headers(reg: Arc<Registry>, identity: fn(&HeaderMap) -> Identity, extra: Vec<(HeaderName, HeaderValue)>) -> Router {
    Router::new().fallback(forward).with_state(Gw { reg, identity, http: reqwest::Client::new(), extra: Arc::new(extra) })
}
```
fazer `router(...)` delegar para `router_with_headers(reg, identity, vec![])`, e em `forward`, após inserir `x-hub-user`:
```rust
for (k, v) in gw.extra.iter() { headers.insert(k.clone(), v.clone()); }
```
(Os cabeçalhos `x-hub-*` do cliente continuam removidos antes; os extras vêm só do hub.)

`platform/crates/hub-cli/src/dev.rs`:
```rust
use anyhow::{Context, Result};
use axum::http::{HeaderMap, HeaderName, HeaderValue};
use hub_gateway::{router_with_headers, Identity};
use hub_registry::{AppEntry, Registry};
use std::{path::Path, sync::{Arc, OnceLock}};

static ROLE: OnceLock<String> = OnceLock::new();

fn dev_identity(_: &HeaderMap) -> Identity {
    Identity { user: "dev.user".into(), roles: vec![ROLE.get().cloned().unwrap_or_else(|| "admin".into())] }
}

fn dev_env(dir: &Path, declared: &[String]) -> Result<Vec<(HeaderName, HeaderValue)>> {
    let path = dir.join(".hub/dev.env");
    let Ok(text) = std::fs::read_to_string(&path) else { return Ok(vec![]) };
    let mut out = Vec::new();
    for line in text.lines().map(str::trim).filter(|l| !l.is_empty() && !l.starts_with('#')) {
        let (k, v) = line.split_once('=').with_context(|| format!("linha inválida em {}: {line}", path.display()))?;
        if !declared.iter().any(|d| d == k) { continue; }
        out.push((HeaderName::try_from(format!("x-hub-env-{}", k.to_ascii_lowercase()))?, HeaderValue::from_str(v)?));
    }
    Ok(out)
}

pub fn dev_router(dir: &Path, upstream: &str, role: &str) -> Result<axum::Router> {
    let _ = ROLE.set(role.to_string());
    let m = hub_manifest::parse(&std::fs::read_to_string(dir.join("manifest.json")).context("manifest.json não encontrado")?)?;
    let env = dev_env(dir, &m.env)?;
    let mut reg = Registry::new();
    reg.register(AppEntry { manifest: m, upstream: upstream.to_string() })?;
    Ok(router_with_headers(Arc::new(reg), dev_identity, env))
}

pub async fn run(dir: &Path, upstream: &str, port: u16, role: &str) -> Result<()> {
    let app = dev_router(dir, upstream, role)?;
    let l = tokio::net::TcpListener::bind(("127.0.0.1", port)).await?;
    println!("hub dev em http://localhost:{port} → {upstream} (papel: {role})");
    axum::serve(l, app).await?;
    Ok(())
}
```

Nota de segurança: em produção, segredos **não** trafegam por cabeçalho — vão para o servidor do app por injeção no deploy (Fase 2). O cabeçalho é exclusivo do modo dev e só para variáveis declaradas.

- [ ] **Step 4: Rodar e ver passar**

Run: `cd platform && cargo test --workspace`
Expected: PASS em todas as crates.

- [ ] **Step 5: Teste manual no Windows sem admin**

`cargo run -p hub-cli -- dev --dir <app> --upstream http://localhost:5000` com um app qualquer rodando nativo; abrir `http://localhost:4173/<id>/`.

- [ ] **Step 6: Commit**

```bash
git add platform/crates/hub-cli platform/crates/hub-gateway
git commit -m "feat(rust-topcoat): hub dev com identidade simulada e variáveis declaradas"
```

---

## Fases seguintes (planos separados, mesma branch)

Cada fase vira um plano próprio em `docs/superpowers/plans/` quando a Fase 1 for aprovada:

| Fase | Entregas | Itens do FAQ |
|---|---|---|
| 2 — Segurança e deploy | JWT assinado + OIDC (SSO), RBAC persistido (Toasty/Postgres), assinatura cosign no `hub pack`, pipeline de validação (7 etapas), API `POST /api/apps/packages`, tela de import, `hub publish/promote/rollback`, OIDC do GitHub Actions, segredos via Vault injetados no deploy | 3, 4, 5, 11 |
| 3 — Operação | Admission control por capacidade do host, `hub dev --limits` (cgroups v2 / Job Objects sem admin / setrlimit), `--container`, health/restart, logs e métricas (OpenTelemetry), tela Containers | 6, 7, 8, 9 |
| 4 — Dados | Backups com restic/Kopia em container auxiliar limitado, hooks pre/post, retenção, restore/clone para staging, teste periódico de restore | 10 |
| 5 — Experiência | Web Components + SDK JS (`@hub/sdk`), busca federada, inbox, tema/white label por tokens, IA e scorecards | Roadmap 12–22 |

## Critério de decisão da stack (ao final da Fase 1)

Registrar em `platform/docs/SPIKE-TOPCOAT.md`:
- Tempo de build limpo/incremental (Windows e CI).
- Esforço para layout/filhos e montagem do gateway no Topcoat.
- Quebras de API encontradas entre versões 0.9.x.
- Ergonomia percebida pela equipe (1–5).

Para comparar com outra stack: criar `stack/<outra>` a partir de `main`, copiar este plano trocando a camada web/runtime e medir os mesmos critérios.
