# Instalação em rede corporativa

## Instalação
- ✅ 34 · Cookie Secure segue a URL (http → auto + aviso; compose prod lê o .prod.env)
- ✅ 35 · Aviso e consentimento no início (padrão N; --yes = consentimento dado)
- ⬜ 36 · Rede corporativa: --proxy, --no-proxy, --ca-file e espelhos (PyPI, imagens, Alpine, Go) + perguntas
- ⬜ 37 · Diagnóstico: --doctor e etapa 0 (proxy, TLS interceptado, CA, espelhos, container, systemd, disco, porta, URL)
- ⬜ 38 · Podman sem sessão systemd: detecção, --no-systemd, socket próprio, --autostart cron
- ⬜ 39 · Escolha do disco: lista montagens graváveis, --data-dir (:Z, dono via podman unshare), --engine-store
- ⬜ 40 · HTTPS no edge: --tls-cert/--tls-key
- ⬜ 41 · Mensagens: caminhos absolutos, erro do bucket visível, reexecução sem perguntas, outros containers, install-cmd.txt, verificação final

## Hub
- ⬜ 42 · Chamadas internas sem proxy (internal_client/external_client, HUB_PROXY/HUB_NO_PROXY, guarda contra httpx direto)
- ⬜ 43 · Build e containers dos apps com proxy, CA e espelhos (buildargs, env, credencial mascarada)

## Docs
- ⬜ 44 · Guia "Instalação em rede corporativa", --help e CHANGELOG

## Fechamento
- ⬜ 45 · hub-check --all, teste em RHEL simulado (rootless sem systemd + proxy com CA própria), limpeza
