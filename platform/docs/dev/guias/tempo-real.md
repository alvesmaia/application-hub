# Tempo real (SSE ou WebSocket)

Mostre as mudanças para todos os usuários sem recarregar a página e **sem JavaScript próprio**: o app
manda HTML e o hub insere na tela. Dois atributos fazem isso:

| Atributo | Transporte | Quando usar |
|---|---|---|
| `data-hub-sse="{{ base }}/eventos"` | SSE (`text/event-stream`), servidor → navegador | o padrão: avisos, quadros, painéis |
| `data-hub-ws="{{ base }}/ws/…"` | WebSocket (só em `/apps/{id}/ws/…`) | quando você precisa de WebSocket; as ações continuam em `data-hub-post` |

As mensagens têm o mesmo formato nos dois. Cada uma é um JSON:

- `{"html": "…", "target": "#id", "swap": "outerHTML"}` troca um trecho da tela (o HTML passa pelo
  sanitizador do hub);
- `{"fragments": [ … ]}` manda até 20 trocas de uma vez;
- `{"text": "…", "target": "#id"}` troca só o texto;
- `{"event": "nome", "data": …}` dispara o evento `hub:sse` (ou `hub:ws`) no contêiner.

Se a conexão cai, o hub reconecta com espera crescente. Enquanto isso, o `data-hub-get` com
`data-hub-trigger="every 5s"` do mesmo contêiner faz polling. Mande sempre o **estado atual**, não as
diferenças: assim, reconectar ou fazer polling dá o mesmo resultado.

O exemplo abaixo usa o projeto do `hub project create` (`app.py`, `store.py` e `templates/`). Troque
`quadro` e `store.list_items` pelo que o seu app mostra.

## 1. Canal de avisos (`hub_sdk.realtime`)

O SDK traz o canal pronto: `from hub_sdk.realtime import canal, sse`. Com Postgres (no hub), o aviso passa
por `LISTEN`/`NOTIFY` e alcança todas as réplicas. Com SQLite (no `hub dev`), o aviso fica no próprio processo.
Não precisa instalar nada além do `hub-sdk` do modelo.

- `await canal.iniciar()` / `await canal.fechar()` no lifespan;
- `canal.mensagens(funcao)` entrega o estado atual na conexão e de novo a cada aviso (`None` = ping);
- `sse(canal.mensagens(...))` devolve a resposta `text/event-stream` pronta;
- `canal.avisar(engine)` depois do commit de cada mudança.

## 2. Rotas e lifespan (`app.py`)

```python
import contextlib
import json
from fastapi import WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from hub_sdk.realtime import canal, sse

# dentro de create_app(), no lifespan que já existe:
    async with sdk.lifespan(app):
        await canal.iniciar()
        yield
        await canal.fechar()

# dentro de create_app(), junto das outras rotas:
    def mensagem_do_quadro(base: str) -> str:
        html = render.render("quadro.html", base=base, items=store.list_items(engine))
        return json.dumps({"html": html})

    @app.get(f"/{APP_ID}/eventos")
    async def eventos(req: Request) -> StreamingResponse:
        base = public_base(req)
        return sse(canal.mensagens(lambda: mensagem_do_quadro(base)))  # o ping mantém a conexão viva

    @app.websocket(f"/{APP_ID}/ws/quadro")  # só se usar data-hub-ws
    async def ws_quadro(ws: WebSocket) -> None:
        await ws.accept()
        base = public_base(ws)
        with contextlib.suppress(WebSocketDisconnect):
            async for m in canal.mensagens(lambda: mensagem_do_quadro(base)):
                if m is not None:  # o hub já manda ping no WebSocket
                    await ws.send_text(m)
```

Em cada rota que muda os dados, depois de gravar:

```python
        store.add_item(engine, item.name, item.quantity)
        canal.avisar(engine)
```

Conexão que cai: no WebSocket, o hub fecha o socket sem resposta em 60 s, e o próximo envio encerra o
laço. No SSE, o servidor percebe no próximo `: ping`.

## 3. Tela (`templates/quadro.html`)

```html
<section id="quadro"
         data-hub-sse="{{ base }}/eventos"
         data-hub-get="{{ base }}/quadro" data-hub-trigger="every 5s">
  {% for i in items %}<p>{{ i.name }}</p>{% endfor %}
</section>
```

O contêiner é o alvo padrão (`outerHTML`). Por isso o HTML enviado traz o próprio
`<section id="quadro" …>` com os mesmos atributos e continua ligado. Para WebSocket, troque
`data-hub-sse="{{ base }}/eventos"` por `data-hub-ws="{{ base }}/ws/quadro"`. O `data-hub-get` aponta
para uma rota que devolve só o `quadro.html` (`render.fragment("quadro.html", …)`), e não a página inteira.

As ações continuam formulários normais, que mandam a resposta para o quadro:

```html
<form method="post" action="{{ base }}/itens" data-hub-target="#quadro">
  <input name="name" aria-label="Nome"><button type="submit" class="primary-button">Adicionar</button>
</form>
```

Com `data-hub-target`, a resposta **inteira** substitui o `#quadro`. Por isso a rota do POST devolve só o
fragmento: `return render.fragment("quadro.html", base=public_base(req), items=store.list_items(engine))`,
depois de `canal.avisar(engine)`. Quem envia vê a resposta na hora; as outras abas recebem pelo SSE ou
WebSocket. Sem `data-hub-target`, a resposta troca a página toda, e a rota devolve a página.

## Testes

- **WebSocket:** o `TestClient` funciona: `with client.websocket_connect("/<id>/ws/quadro") as ws:
  ws.receive_text()`. Pelo hub (porta do `hub dev`), mande também `Origin: http://localhost:4173`. Sem o
  `Origin` igual ao `Host`, o hub recusa com 403.
- **SSE:** o `TestClient` espera a resposta inteira, então **trava num fluxo infinito**. Teste o canal
  direto:

```python
import asyncio

from hub_sdk.realtime import Canal


def test_canal_acorda_as_conexoes() -> None:
    async def caso() -> None:
        c = Canal()
        await c.iniciar()
        n = iter(range(10))
        fluxo = c.mensagens(lambda: str(next(n)))
        assert await anext(fluxo) == "0"  # estado atual ao conectar
        proximo = asyncio.ensure_future(anext(fluxo))
        await asyncio.sleep(0)
        c._acordar()
        assert await proximo == "1"
        await fluxo.aclose()

    asyncio.run(caso())
```

Para testar de ponta a ponta, abra duas abas no `hub dev` e mude algo numa delas. Por linha de comando:
`curl -N http://localhost:4173/_frag/<id>/eventos`.
