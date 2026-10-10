# Limpeza de volumes órfãos — 2026-09-29

Contexto: 281 volumes dangling encontrados (`docker volume ls -qf dangling=true`).
Critério de remoção: CreatedAt dentro de 2026-09-27..2026-09-29 (janela das execuções de CI)
E fingerprint de conteúdo comprovando origem do projeto (inspecionado via container
`alpine` montando o volume só leitura; para os de Postgres, subido um postgres:16-alpine
temporário apontando para o volume e consultado `select datname from pg_database`).

## docker system df — antes/depois
```
TYPE            TOTAL     ACTIVE    SIZE      RECLAIMABLE
Images          74        21        159.6GB   142GB (88%)
Containers      30        0         5.407GB   5.407GB (100%)
Local Volumes   305       24        48.58GB   25.12GB (51%)
Build Cache     615       0         81.87MB   81.87MB

TYPE            TOTAL     ACTIVE    SIZE      RECLAIMABLE
Images          74        21        159.6GB   142GB (88%)
Containers      30        0         5.407GB   5.407GB (100%)
Local Volumes   257       24        32.38GB   8.926GB (27%)
Build Cache     615       0         81.87MB   81.87MB
```

Local Volumes: 48.58GB -> 32.38GB (freed ~16.2GB de espaço em disco reclamável;
dados brutos somados dos 48 volumes removidos: ~14.6GB antes de deduplicação/compressão).

## Removidos (48)

| Volume | CreatedAt | Tamanho | Motivo |
|---|---|---|---|
| `0ae66b5caec4f49c3130...` | 2026-09-29T05:10:12Z | 335900K | anônimo do hub-testdb (postgres:16-alpine); conteúdo confirmado via pg_database: bancos sdkt_*/hubt_*/app_fornecedores* (padrão de nomes dos testes deste repo) |
| `0ba21ed1dab591837915...` | 2026-09-28T11:57:48Z | 55836K | anônimo do hub-testdb (postgres:16-alpine); conteúdo confirmado via pg_database: bancos sdkt_*/hubt_*/app_fornecedores* (padrão de nomes dos testes deste repo) |
| `0e43822c3f6c8f589d47...` | 2026-09-28T12:45:22Z | 456164K | anônimo do hub-testdb (postgres:16-alpine); conteúdo confirmado via pg_database: bancos sdkt_*/hubt_*/app_fornecedores* (padrão de nomes dos testes deste repo) |
| `1d85f8bf139ec1820908...` | 2026-09-29T11:54:34Z | 339540K | anônimo do hub-testdb (postgres:16-alpine); conteúdo confirmado via pg_database: bancos sdkt_*/hubt_*/app_fornecedores* (padrão de nomes dos testes deste repo) |
| `1f04fb7b10c2ad6331fd...` | 2026-09-29T04:18:28Z | 339932K | anônimo do hub-testdb (postgres:16-alpine); conteúdo confirmado via pg_database: bancos sdkt_*/hubt_*/app_fornecedores* (padrão de nomes dos testes deste repo) |
| `02f6e5f94a94bcb17b40...` | 2026-09-28T11:54:13Z | 397896K | anônimo do hub-testdb (postgres:16-alpine); conteúdo confirmado via pg_database: bancos sdkt_*/hubt_*/app_fornecedores* (padrão de nomes dos testes deste repo) |
| `3c1b1c7a12a4ffdbc4dd...` | 2026-09-29T02:27:20Z | 339716K | anônimo do hub-testdb (postgres:16-alpine); conteúdo confirmado via pg_database: bancos sdkt_*/hubt_*/app_fornecedores* (padrão de nomes dos testes deste repo) |
| `3dd723d2a9a777e2362c...` | 2026-09-28T07:37:27Z | 319820K | anônimo do hub-testdb (postgres:16-alpine); conteúdo confirmado via pg_database: bancos sdkt_*/hubt_*/app_fornecedores* (padrão de nomes dos testes deste repo) |
| `05a0c162eaef300b6aa1...` | 2026-09-29T11:42:53Z | 118384K | anônimo do hub-testdb (postgres:16-alpine); conteúdo confirmado via pg_database: bancos sdkt_*/hubt_*/app_fornecedores* (padrão de nomes dos testes deste repo) |
| `05d060886295bdf53be9...` | 2026-09-27T22:17:28Z | 56028K | anônimo do hub-testdb (postgres:16-alpine); conteúdo confirmado via pg_database: bancos sdkt_*/hubt_*/app_fornecedores* (padrão de nomes dos testes deste repo) |
| `6ddc8bd7bcd552ec172d...` | 2026-09-28T08:34:23Z | 316388K | anônimo do hub-testdb (postgres:16-alpine); conteúdo confirmado via pg_database: bancos sdkt_*/hubt_*/app_fornecedores* (padrão de nomes dos testes deste repo) |
| `7e9e2fc1d3d7bc795cd3...` | 2026-09-29T11:41:31Z | 339576K | anônimo do hub-testdb (postgres:16-alpine); conteúdo confirmado via pg_database: bancos sdkt_*/hubt_*/app_fornecedores* (padrão de nomes dos testes deste repo) |
| `8f750426f1dfd3068d3b...` | 2026-09-28T20:55:28Z | 339904K | anônimo do hub-testdb (postgres:16-alpine); conteúdo confirmado via pg_database: bancos sdkt_*/hubt_*/app_fornecedores* (padrão de nomes dos testes deste repo) |
| `9f9a4003b339ecbaebbb...` | 2026-09-29T11:34:00Z | 339620K | anônimo do hub-testdb (postgres:16-alpine); conteúdo confirmado via pg_database: bancos sdkt_*/hubt_*/app_fornecedores* (padrão de nomes dos testes deste repo) |
| `16ecf70410a12664d121...` | 2026-09-28T05:25:05Z | 55804K | anônimo do hub-testdb (postgres:16-alpine); conteúdo confirmado via pg_database: bancos sdkt_*/hubt_*/app_fornecedores* (padrão de nomes dos testes deste repo) |
| `21bcf6a3dddbd9e3857d...` | 2026-09-29T11:43:32Z | 70956K | anônimo do hub-testdb (postgres:16-alpine); conteúdo confirmado via pg_database: bancos sdkt_*/hubt_*/app_fornecedores* (padrão de nomes dos testes deste repo) |
| `31a726961462f0602a17...` | 2026-09-27T03:51:07Z | 173760K | anônimo do hub-testdb (postgres:16-alpine); conteúdo confirmado via pg_database: bancos sdkt_*/hubt_*/app_fornecedores* (padrão de nomes dos testes deste repo) |
| `47a3c39f92b6052f5e69...` | 2026-09-29T05:17:03Z | 374964K | anônimo do hub-testdb (postgres:16-alpine); conteúdo confirmado via pg_database: bancos sdkt_*/hubt_*/app_fornecedores* (padrão de nomes dos testes deste repo) |
| `50d07b49f3f490d81be4...` | 2026-09-27T05:32:29Z | 458160K | anônimo do hub-testdb (postgres:16-alpine); conteúdo confirmado via pg_database: bancos sdkt_*/hubt_*/app_fornecedores* (padrão de nomes dos testes deste repo) |
| `181f1a5472c81dfa3e82...` | 2026-09-27T19:40:29Z | 39852K | anônimo do hub-testdb (postgres:16-alpine); conteúdo confirmado via pg_database: bancos sdkt_*/hubt_*/app_fornecedores* (padrão de nomes dos testes deste repo) |
| `593bd4d5371492ce2ec7...` | 2026-09-28T05:23:01Z | 365256K | anônimo do hub-testdb (postgres:16-alpine); conteúdo confirmado via pg_database: bancos sdkt_*/hubt_*/app_fornecedores* (padrão de nomes dos testes deste repo) |
| `0649acf505699662e11c...` | 2026-09-29T11:43:06Z | 180260K | anônimo do hub-testdb (postgres:16-alpine); conteúdo confirmado via pg_database: bancos sdkt_*/hubt_*/app_fornecedores* (padrão de nomes dos testes deste repo) |
| `795a8578f3dcbf8eadce...` | 2026-09-27T03:50:56Z | 46792K | anônimo do hub-testdb (postgres:16-alpine); conteúdo confirmado via pg_database: bancos sdkt_*/hubt_*/app_fornecedores* (padrão de nomes dos testes deste repo) |
| `0934fd62657981bc44be...` | 2026-09-28T21:48:10Z | 339928K | anônimo do hub-testdb (postgres:16-alpine); conteúdo confirmado via pg_database: bancos sdkt_*/hubt_*/app_fornecedores* (padrão de nomes dos testes deste repo) |
| `2050f2ece6481bed1e8c...` | 2026-09-27T23:08:34Z | 148596K | anônimo do hub-testdb (postgres:16-alpine); conteúdo confirmado via pg_database: bancos sdkt_*/hubt_*/app_fornecedores* (padrão de nomes dos testes deste repo) |
| `43804a09685025ef0958...` | 2026-09-27T03:12:38Z | 1250016K | anônimo do hub-testdb (postgres:16-alpine); conteúdo confirmado via pg_database: bancos sdkt_*/hubt_*/app_fornecedores* (padrão de nomes dos testes deste repo) |
| `91157b20fb12370d1ae2...` | 2026-09-29T11:33:01Z | 339592K | anônimo do hub-testdb (postgres:16-alpine); conteúdo confirmado via pg_database: bancos sdkt_*/hubt_*/app_fornecedores* (padrão de nomes dos testes deste repo) |
| `927828e50ad01b9b853d...` | 2026-09-29T03:31:27Z | 339940K | anônimo do hub-testdb (postgres:16-alpine); conteúdo confirmado via pg_database: bancos sdkt_*/hubt_*/app_fornecedores* (padrão de nomes dos testes deste repo) |
| `9890773ede7ebeb73819...` | 2026-09-29T05:37:11Z | 339812K | anônimo do hub-testdb (postgres:16-alpine); conteúdo confirmado via pg_database: bancos sdkt_*/hubt_*/app_fornecedores* (padrão de nomes dos testes deste repo) |
| `45286145a1d9a7e2240d...` | 2026-09-27T08:44:27Z | 352636K | anônimo do hub-testdb (postgres:16-alpine); conteúdo confirmado via pg_database: bancos sdkt_*/hubt_*/app_fornecedores* (padrão de nomes dos testes deste repo) |
| `224776763f5efebaedad...` | 2026-09-29T11:35:55Z | 339612K | anônimo do hub-testdb (postgres:16-alpine); conteúdo confirmado via pg_database: bancos sdkt_*/hubt_*/app_fornecedores* (padrão de nomes dos testes deste repo) |
| `a66c48059b81d915577a...` | 2026-09-29T11:34:57Z | 339612K | anônimo do hub-testdb (postgres:16-alpine); conteúdo confirmado via pg_database: bancos sdkt_*/hubt_*/app_fornecedores* (padrão de nomes dos testes deste repo) |
| `ab43c0af2f34cd189fa5...` | 2026-09-27T19:40:49Z | 39852K | anônimo do hub-testdb (postgres:16-alpine); conteúdo confirmado via pg_database: bancos sdkt_*/hubt_*/app_fornecedores* (padrão de nomes dos testes deste repo) |
| `b22cab344ef9d4b02a23...` | 2026-09-27T10:40:15Z | 292044K | anônimo do hub-testdb (postgres:16-alpine); conteúdo confirmado via pg_database: bancos sdkt_*/hubt_*/app_fornecedores* (padrão de nomes dos testes deste repo) |
| `b742526b2b764a9e1e86...` | 2026-09-28T14:48:29Z | 340380K | anônimo do hub-testdb (postgres:16-alpine); conteúdo confirmado via pg_database: bancos sdkt_*/hubt_*/app_fornecedores* (padrão de nomes dos testes deste repo) |
| `be8b9b218055122b80e3...` | 2026-09-27T07:04:19Z | 321028K | anônimo do hub-testdb (postgres:16-alpine); conteúdo confirmado via pg_database: bancos sdkt_*/hubt_*/app_fornecedores* (padrão de nomes dos testes deste repo) |
| `c6c14ce69b6c4e342c39...` | 2026-09-28T21:22:49Z | 339836K | anônimo do hub-testdb (postgres:16-alpine); conteúdo confirmado via pg_database: bancos sdkt_*/hubt_*/app_fornecedores* (padrão de nomes dos testes deste repo) |
| `c8958e0ec6bf1a96152d...` | 2026-09-27T18:45:34Z | 1247524K | anônimo do hub-testdb (postgres:16-alpine); conteúdo confirmado via pg_database: bancos sdkt_*/hubt_*/app_fornecedores* (padrão de nomes dos testes deste repo) |
| `ca9baff3014a5f17c375...` | 2026-09-28T07:35:01Z | 371656K | anônimo do hub-testdb (postgres:16-alpine); conteúdo confirmado via pg_database: bancos sdkt_*/hubt_*/app_fornecedores* (padrão de nomes dos testes deste repo) |
| `d1e49f905a27f2d41e5d...` | 2026-09-29T11:43:19Z | 207220K | anônimo do hub-testdb (postgres:16-alpine); conteúdo confirmado via pg_database: bancos sdkt_*/hubt_*/app_fornecedores* (padrão de nomes dos testes deste repo) |
| `da61cfd2d3d8659685d2...` | 2026-09-27T15:19:09Z | 319800K | anônimo do hub-testdb (postgres:16-alpine); conteúdo confirmado via pg_database: bancos sdkt_*/hubt_*/app_fornecedores* (padrão de nomes dos testes deste repo) |
| `dc626f59d6ae6bdd339b...` | 2026-09-29T02:44:01Z | 339888K | anônimo do hub-testdb (postgres:16-alpine); conteúdo confirmado via pg_database: bancos sdkt_*/hubt_*/app_fornecedores* (padrão de nomes dos testes deste repo) |
| `e26478f5886bf8392fe0...` | 2026-09-29T11:31:23Z | 339424K | anônimo do hub-testdb (postgres:16-alpine); conteúdo confirmado via pg_database: bancos sdkt_*/hubt_*/app_fornecedores* (padrão de nomes dos testes deste repo) |
| `ec1692c16b5a188b0ee0...` | 2026-09-28T04:03:34Z | 315884K | anônimo do hub-testdb (postgres:16-alpine); conteúdo confirmado via pg_database: bancos sdkt_*/hubt_*/app_fornecedores* (padrão de nomes dos testes deste repo) |
| `f2b5245b1376bd8cf470...` | 2026-09-27T19:53:58Z | 85316K | anônimo do hub-testdb (postgres:16-alpine); conteúdo confirmado via pg_database: bancos sdkt_*/hubt_*/app_fornecedores* (padrão de nomes dos testes deste repo) |
| `f6fd2288caf447c7304d...` | 2026-09-27T08:45:36Z | 78424K | anônimo do hub-testdb (postgres:16-alpine); conteúdo confirmado via pg_database: bancos sdkt_*/hubt_*/app_fornecedores* (padrão de nomes dos testes deste repo) |
| `f1670508de7155289da7...` | 2026-09-27T04:00:55Z | 1048600K | anônimo do hub-testdb (postgres:16-alpine); conteúdo confirmado via pg_database: bancos sdkt_*/hubt_*/app_fornecedores* (padrão de nomes dos testes deste repo) |
| `c72862947052bd4620f6...` | 2026-09-27T08:05:15Z | 104K | anônimo do VOLUME /data declarado em Dockerfile.hub-web; conteúdo confirmado (admin.json/apps/auth.json/bootstrap-admin.txt/outbox com app ativos/fornecedores) = dados de HUB_DATA_DIR de execução de teste |

## Não removidos (dono incerto) — deixados intactos

- **208 volumes anônimos vazios** (4.0K, dono uid 70, modo 1777, mesma janela de datas).
  Conteúdo vazio não permite confirmar origem; não batem com o volume de dados do
  postgres:16-alpine (que é 0700, não 1777) nem com nenhum `VOLUME` declarado nos
  Dockerfiles deste repo. Podem ser artefatos de outra imagem/projeto. Não removidos.
- **1 volume anônimo com `docker/registry/...`** (ex.: `5613f6f0...`, criado 2026-09-27):
  parece um registry Docker local; não corresponde a nada deste repositório (o hub não roda
  registry próprio). Não removido.
- **hub-scratch-ativos__staging** e **hub-scratch-fornecedores__staging** (volumes NOMEADOS,
  não anônimos): claramente do nosso projeto (prefixo hub-, apps ativos/fornecedores), mas
  são volumes nomeados que podem ser reaproveitados por um próximo deploy de staging — não
  removidos por precaução (não são "vazamento" de container descartável, e sim estado
  esperado de um volume nomeado ocioso no momento da inspeção).
- **hub-audit-cache** (volume nomeado, cache de cargo/cargo-audit/cargo-deny usado por
  `st_audit_deny`): intencionalmente persistente, não removido.
- **21 volumes fora da janela 2026-09-27..2026-09-29** (datas de 2025-11 a 2026-07/09-21):
  não inspecionados a fundo (fora do critério temporal do CI recente); não removidos.
