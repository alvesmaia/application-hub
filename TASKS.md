# Instalação em rede corporativa

## Instalação
- ✅ 34 · Cookie Secure segue a URL (http → auto + aviso; compose prod lê o .prod.env)
- ✅ 35 · Aviso e consentimento no início (padrão N; --yes = consentimento dado)
- ✅ 36 · Rede corporativa: --proxy, --no-proxy, --ca-file e espelhos (PyPI, imagens, Alpine, Go) + perguntas
- ✅ 37 · Diagnóstico: --doctor e etapa 0 (proxy, TLS interceptado, CA, espelhos, container, systemd, disco, porta, URL)
- ✅ 38 · Podman sem sessão systemd: detecção, --no-systemd, socket próprio, --autostart cron
- ✅ 39 · Escolha do disco: lista montagens graváveis, --data-dir (:Z, dono via podman unshare), --engine-store
- ✅ 40 · HTTPS no edge: --tls-cert/--tls-key
- ✅ 41 · Mensagens: caminhos absolutos, erro do bucket visível, reexecução sem perguntas, outros containers, install-cmd.txt, verificação final

## Hub
- ✅ 42 · Chamadas internas sem proxy (internal_client/external_client, HUB_PROXY/HUB_NO_PROXY, guarda contra httpx direto)
- ✅ 43 · Build e containers dos apps com proxy, CA e espelhos (buildargs, env, credencial mascarada)

## Docs
- ✅ 44 · Guia "Instalação em rede corporativa", --help e CHANGELOG

## Fechamento
- ✅ 45 · hub-check --all, teste em RHEL simulado (rootless sem systemd + proxy com CA própria), limpeza
  - ✅ hub-check --all verde (30/30)
  - ✅ laboratório: Rocky 9 + Podman 5.8.2 rootless sem systemd, só saída por proxy que inspeciona TLS (mitmproxy)
  - ✅ --doctor no laboratório: aponta proxy, emissor do certificado e --ca-file; com as opções, tudo ✔
  - ✅ 6 falhas reais corrigidas com teste: download do compose, pulls do engine sem CA, .prod.env com CR, socket sem setsid, linha da CA no template, senha do proxy no histórico (Podman)
  - ✅ instalação completa no laboratório (0–5/5, MinIO e /readyz alcançados de dentro do hub)
  - ✅ app novo do template publicado em staging pelo hub (build pelo proxy com a CA, blue/green, prontidão ok)
  - ✅ limpeza: rhel-corp-* (contêineres, rede, volume anônimo), imagens Rocky/mitmproxy, sdk-python/build
