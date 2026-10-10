# Estudo: atualização sem parada e com reversão automática

Situação em 2026-10-10. Hoje, `dc up -d` recria o `app-hub-web` e o hub fica fora do ar de 3 a 11 s. Nesse
intervalo param o portal, o gateway dos apps, a API/MCP e as conexões SSE e WebSocket. A volta atrás é
manual (`docs/ATUALIZACAO.md` §5).

## O que já existe e ajuda

- **Réplicas do hub já funcionam:** o lease `hub-leader` no banco de estado (`hub/ops/leader.py`) garante que só
  uma réplica rode o que precisa ser único (outbox, agendador de backups, varreduras). Os arquivos de `/data`
  usam trava entre processos (`hub/filelock.py`).
- **`/healthz` informa a versão,** e os apps já fazem blue/green com sonda de prontidão (`/_hub/ready`).
- **Tempo real reconecta sozinho:** `data-hub-ws` e `data-hub-sse` voltam com espera crescente e polling enquanto
  isso.

## Opções

| | Como funciona | Parada | Custo |
|---|---|---|---|
| A. Só reiniciar mais rápido | imagem pronta antes, `up --no-deps` | 2–4 s | baixo, mas não resolve |
| **B. Azul/verde atrás de um proxy** | proxy fixo na porta do hub; sobe a cor nova, testa e troca | **0 s** | médio |
| C. Orquestrador (Swarm/K8s) | rolling update nativo | 0 s | alto; muda a instalação inteira |

**Recomendação: B.** Usa o que já existe (réplicas, líder, travas) e não muda a forma de instalar.

## Desenho da opção B

1. **Proxy de borda** `app-hub-edge` (Caddy, imagem oficial), único serviço na `HUB_PORT`. Os contêineres
   `app-hub-web-blue` e `app-hub-web-green` ficam só na rede interna. O proxy repassa HTTP, SSE e WebSocket
   sem buffer e drena as conexões na troca.
2. **Comando `hub-update <versão>`** (em `platform-py/scripts/`), em passos que param no primeiro erro:
   1. faz o backup do §1 de `ATUALIZACAO.md` automaticamente e guarda a imagem atual com a tag `rollback`;
   2. baixa ou constrói a versão nova e sobe a cor inativa;
   3. espera `/healthz` com a versão nova e `/readyz`. Hoje o `/readyz` só responde "ready"; ele passa a
      conferir o banco de estado, as migrações aplicadas e a leitura de `/data`. Depois roda testes rápidos (login, `/developers`, um `/apps/<id>`);
   4. troca o upstream do proxy pela API de administração do Caddy, sem reiniciar;
   5. drena a cor antiga (SIGTERM; o uvicorn termina as requisições em curso) e a mantém parada para a volta
      atrás.
3. **Reversão automática:**
   - **Antes da troca:** qualquer falha remove a cor nova. Nada mudou para os usuários.
   - **Depois da troca:** durante 5 min, o comando observa os erros 5xx e o `/healthz`. Se piorar, volta o
     proxy para a cor antiga, que ainda existe, em menos de 1 s.
   - **Dados:** só é possível voltar sem restaurar se as migrações forem **aditivas** (expandir agora, remover
     colunas e campos só na versão seguinte). Cada release declara `rollback_compat: <versão mínima>`. Se a
     versão antiga não for compatível, o comando avisa antes de começar e a volta atrás exige restaurar o
     backup, com confirmação.
4. **Aviso de versão nova:** opcional (`HUB_UPDATE_CHECK=1`). O hub consulta as releases do repositório uma vez
   por dia e mostra um aviso em Administração › Saúde com o comando de atualização. Nada atualiza sozinho.

## Riscos

- **Duas versões no ar durante segundos:** a cor antiga e a nova leem o mesmo `/data` e o mesmo banco. Por isso
  a regra de migração aditiva é obrigatória, e um teste do CI sobe a versão anterior sobre os dados da nova.
- **Memória:** duas cópias do hub por alguns segundos (~+250 MB no pico).
- **Porta e TLS:** o proxy passa a ser o dono da `HUB_PORT`. Quem usa túnel ou proxy externo não muda nada,
  porque o destino continua o mesmo.

## Esforço estimado

Proxy e compose: 1 dia. `hub-update` com reversão: 2 dias. `/readyz` de verdade e regra de migração aditiva com teste
de compatibilidade: 1–2 dias. Aviso de versão: meio dia. Validação (caos durante a troca, carga com SSE e
WebSocket abertos): 1 dia.
