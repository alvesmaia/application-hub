# Instruções para agentes

Você é um agente de código. Siga estes passos para criar e publicar um app neste Application Hub
(`@HUB@`). Se o pedido não disser o que o app faz, pergunte antes de começar.

## 1. Instalar a CLI

Pré-requisito: **uv** e Python 3.12. A CLI vem do índice público deste hub:

```bash
uv tool install hub-cli --index @HUB@/dev/cli/simple/
hub --help
```

## 2. Criar o projeto

Escolha um id (2–32 caracteres: `a-z`, `0-9`, `-`) e gere o projeto sem perguntas:

```bash
hub project create <id-do-app> --stack python-fastapi --yes --json
cd <id-do-app> && uv sync
```

## 3. Ler as regras

- O `AGENTS.md` gerado na raiz do projeto: estrutura, comandos e regras do template.
- O contrato do app: [@HUB@/dev/docs/contrato.md](@HUB@/dev/docs/contrato.md) — o que o hub exige de todo app.
- Referências: [CLI](@HUB@/dev/docs/cli.md) e [SDK](@HUB@/dev/docs/sdk.md).

## 4. Desenvolver

- Sem JavaScript próprio: comportamentos de tela com atributos `data-hub-*`.
- Links sempre pela base pública do app (`{{ base }}` nos templates), nunca `/<id>/…` cru.
- Toda tela e rota nova com teste.

## 5. Verificar

```bash
hub check   # lint, tipos, testes, manifesto, openapi.json e conformidade
hub dev     # hub local com o app no shell: http://localhost:4173/apps/<id-do-app>
```

Repita até o `hub check` ficar verde.

## 6. Publicar

O token vem do dono do hub; a CLI pede o token sem eco e o guarda fora do projeto:

```bash
hub login --hub @HUB@
hub deploy --env staging --yes
```

Sem acesso ao hub, gere o pacote e entregue o arquivo `.hubapp`:

```bash
hub pack --build-dir .
```

## Regras

- Nunca faça commit de segredos (`.env`, tokens, chaves); nunca escreva o token em arquivos do projeto.
- Produção (`--env production`) só com aprovação no hub — publique em staging e peça a aprovação.
