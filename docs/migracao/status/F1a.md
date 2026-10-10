# F1a — Vault e state

Portados em `platform-py/hub/vault` e `platform-py/hub/state`.

- Vault: chave mestra hex de 32 bytes (`HUB_MASTER_KEY_FILE` ou `master.key`), envelope AES-256-GCM com nonce de 12 bytes e AAD, keyring v2/upgrade v1, metadados protegidos com geração, assinatura Ed25519, rotação de DEK/KEK e retomada via `.key.new`. Arquivos stream v2 e leitura v1.
- State: migração com as mesmas oito tabelas, records/tokens, sessões, janela fixa de rate limit, leases/fencing/renovação, réplicas, locks de arquivo e distribuídos, jobs, KV e limpeza. SQL usa os mesmos nomes e colunas do Rust.

Compatibilidade verificada por `tests/vectors/rust-vault-v2.json` (vetor Rust fixo), keyring Python aberto pelo Rust e cifra/decifra bidirecional de arquivos stream. `cargo test -p hub-vault`: 14 testes passaram. `platform-py/scripts/check.sh` com Postgres descartável: 41 testes passaram; ruff, formato e mypy strict passaram. Container `hubpy-testdb` parado após a execução.

Gaps: APIs Python são async (sem wrappers `*_sync` do Rust). `Vault` ainda não conecta seu reload do keyring ao lock de geração distribuído; o módulo state já oferece o lock PostgreSQL, mas falta integrar esse ciclo ao uso multi-réplica do cofre. KMS externo segue fora do escopo, como no crate Rust.
