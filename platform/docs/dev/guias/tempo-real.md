# Real-time (SSE or WebSocket)

Show changes to all users without reloading the page and **without app JavaScript of its own**: the app
sends HTML and the hub inserts it into the screen. Two attributes do this:

| Attribute | Transport | When to use |
|---|---|---|
| `data-hub-sse="{{ base }}/eventos"` | SSE (`text/event-stream`), server → browser | the default: notices, boards, dashboards |
| `data-hub-ws="{{ base }}/ws/…"` | WebSocket (only at `/apps/{id}/ws/…`) | when you need WebSocket; actions still use `data-hub-post` |

The messages have the same format in both. Each one is a JSON object:

- `{"html": "…", "target": "#id", "swap": "outerHTML"}` replaces a piece of the screen (the HTML goes through the
  hub sanitizer);
- `{"fragments": [ … ]}` sends up to 20 swaps at once;
- `{"text": "…", "target": "#id"}` replaces only the text;
- `{"event": "nome", "data": …}` fires the `hub:sse` (or `hub:ws`) event on the container.

If the connection drops, the hub reconnects with increasing back-off. Meanwhile, the `data-hub-get` with
`data-hub-trigger="every 5s"` on the same container polls. Always send the **current state**, not the differences: this way,
reconnecting or polling gives the same result.

The example below uses the project from `hub project create` (`app.py`, `store.py` and `templates/`). Replace
`quadro` and `store.list_items` with what your app shows.

## 1. Notification channel (`hub_sdk.realtime`)

The SDK ships the channel ready: `from hub_sdk.realtime import channel, sse`. With Postgres (in the hub), the notice goes
through `LISTEN`/`NOTIFY` and reaches all replicas. With SQLite (in `hub dev`), the notice stays in the process itself.
Nothing to install beyond the `hub-sdk` from the template.

- `await channel.start()` / `await channel.close()` in the lifespan;
- `channel.messages(funcao)` delivers the current state on the connection and again on each notice (`None` = ping);
- `sse(channel.messages(...))` returns the ready `text/event-stream` response;
- `channel.notify(engine)` after the commit of each change.

## 2. Routes and lifespan (`app.py`)

```python
import contextlib
import json
from fastapi import WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from hub_sdk.realtime import channel, sse

# inside create_app(), in the existing lifespan:
    async with sdk.lifespan(app):
        await channel.start()
        yield
        await channel.close()

# inside create_app(), next to the other routes:
    def mensagem_do_quadro(base: str) -> str:
        html = render.render("quadro.html", base=base, items=store.list_items(engine))
        return json.dumps({"html": html})

    @app.get(f"/{APP_ID}/eventos")
    async def eventos(req: Request) -> StreamingResponse:
        base = public_base(req)
        return sse(channel.messages(lambda: mensagem_do_quadro(base)))  # the ping keeps the connection alive

    @app.websocket(f"/{APP_ID}/ws/quadro")  # only if you use data-hub-ws
    async def ws_quadro(ws: WebSocket) -> None:
        await ws.accept()
        base = public_base(ws)
        with contextlib.suppress(WebSocketDisconnect):
            async for m in channel.messages(lambda: mensagem_do_quadro(base)):
                if m is not None:  # the hub already sends ping over WebSocket
                    await ws.send_text(m)
```

In each route that changes data, after writing:

```python
        store.add_item(engine, item.name, item.quantity)
        channel.notify(engine)
```

Dropped connection: over WebSocket, the hub closes the socket without a response after 60 s, and the next send ends the
loop. Over SSE, the server notices on the next `: ping`.

## 3. Screen (`templates/quadro.html`)

```html
<section id="quadro"
         data-hub-sse="{{ base }}/eventos"
         data-hub-get="{{ base }}/quadro" data-hub-trigger="every 5s">
  {% for i in items %}<p>{{ i.name }}</p>{% endfor %}
</section>
```

The container is the default target (`outerHTML`). That is why the HTML sent carries its own
`<section id="quadro" …>` with the same attributes and stays connected. For WebSocket, replace
`data-hub-sse="{{ base }}/eventos"` with `data-hub-ws="{{ base }}/ws/quadro"`. The `data-hub-get` points
to a route that returns only `quadro.html` (`render.fragment("quadro.html", …)`), not the whole page.

Actions remain normal forms, which send the response to the board:

```html
<form method="post" action="{{ base }}/itens" data-hub-target="#quadro">
  <input name="name" aria-label="Nome"><button type="submit" class="primary-button">Adicionar</button>
</form>
```

With `data-hub-target`, the **whole** response replaces `#quadro`. For that reason the POST route returns only the
fragment: `return render.fragment("quadro.html", base=public_base(req), items=store.list_items(engine))`,
after `channel.notify(engine)`. Whoever sends sees the response right away; the other tabs receive it through SSE or
WebSocket. Without `data-hub-target`, the response replaces the whole page, and the route returns the page.

## Tests

- **WebSocket:** `TestClient` works: `with client.websocket_connect("/<id>/ws/quadro") as ws:
  ws.receive_text()`. Through the hub (`hub dev` port), also send `Origin: http://localhost:4173`. Without an
  `Origin` equal to `Host`, the hub refuses with 403.
- **SSE:** `TestClient` waits for the whole response, so it **hangs on an infinite stream**. Test the channel
  directly:

```python
import asyncio

from hub_sdk.realtime import Channel


def test_channel_wakes_connections() -> None:
    async def caso() -> None:
        c = Channel()
        await c.start()
        n = iter(range(10))
        fluxo = c.messages(lambda: str(next(n)))
        assert await anext(fluxo) == "0"  # current state on connect
        proximo = asyncio.ensure_future(anext(fluxo))
        await asyncio.sleep(0)
        c._wake()
        assert await proximo == "1"
        await fluxo.aclose()

    asyncio.run(caso())
```

For end-to-end testing, open two tabs in `hub dev` and change something in one of them. From the command line:
`curl -N http://localhost:4173/_frag/<id>/eventos`.
