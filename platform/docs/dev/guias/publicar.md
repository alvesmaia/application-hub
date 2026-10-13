# Publicar em staging e produção

## Staging

```bash
hub login --hub @HUB@
hub deploy --env staging --yes
```

O hub valida o pacote (manifesto, contrato, imagem), provisiona o que o manifesto pede em `requires` e implanta em
blue/green. Abra o app em staging pelo seletor de ambiente do hub.

## Produção

```bash
hub deploy --env production --yes
```

A versão fica **aguardando aprovação** na tela Implantações; quem aprova é um operador do hub.

## Variáveis e segredos

Os valores de `requires.variables` e `requires.secrets` vêm de `deploy.<env>.env` (ou são perguntados; segredo sem
eco). A CLI só mostra os nomes, nunca os valores. Não faça commit desses arquivos.

## Sem acesso ao hub

```bash
hub pack --build-dir .
```

Gera o `.hubapp` para envio manual na tela Implantações (mesmo pipeline e provisionamento).

O pacote vai até **20 MB**. Ele leva o manifesto, o `openapi.json`, a pasta `ui/` e o contexto de build
(`build/**`) — **não** as dependências: quem as instala é o `Dockerfile`, durante o build no hub. Se estourar o
limite, o `hub pack` diz quais são os maiores arquivos; o caso comum é um `.venv/` ou um `vendor/` copiado para
`build/` (acrescente ao `.dockerignore`).

## Códigos de saída

`0` ok · `1` pacote rejeitado · `2` validação · `3` token recusado · `4` hub inacessível.
