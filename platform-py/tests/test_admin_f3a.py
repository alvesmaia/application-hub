"""Administração (F3a): Usuários, Funções e permissões, Variáveis de ambiente; RBAC, auditoria e segredos."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import cast

import pytest
from admin_helper import PASSWORD, add_user, make_hub
from fastapi.testclient import TestClient
from httpx import Response

from hub.admin.config import bound_to, display_value, is_platform_env, masked, resolve
from hub.admin.routes import admin_level, initials, relative, same_roles
from hub.auth import api_required, user_view
from hub.ux import search as srch

SECRET = "sk_live_super-secreto-123456"


def dele(c: TestClient, url: str) -> Response:
    return cast(Response, c.request("DELETE", url, headers={"content-type": "application/json"}))


@pytest.fixture
def hub(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    return make_hub(tmp_path, monkeypatch)


def audit_titles(c: TestClient) -> list[str]:
    return [e["title"] for e in asyncio.run(c.app.state.admin.cfg.read())["audit"]]  # type: ignore[attr-defined]


# ---- puras ------------------------------------------------------------------------------------


def test_pure_helpers() -> None:
    assert display_value({"kind": "publica", "value": "abc"}) == "abc"
    assert display_value({"kind": "secreta", "value": "short"}) == "••••••••"
    assert display_value({"kind": "secreta", "value": "0123456789abcd"}) == "••••••••abcd"
    assert display_value({"kind": "connection_string", "value": "postgres://u:p@h/db"}) == "••••••••"
    var = {"name": "A", "value": SECRET, "kind": "secreta", "bindings": [], "inject_as": "B", "managed": True}
    assert SECRET not in json.dumps(masked(var)) and masked(var)["inject_as"] == "B"
    assert is_platform_env("HUB_STORAGE_TOKEN_ATIVOS_PRODUCAO") and not is_platform_env("HUB_STORAGE")
    env = [
        {
            "name": "X",
            "value": "1",
            "kind": "publica",
            "bindings": [{"app": "a", "env": "producao"}],
            "inject_as": "Y",
        }
    ]
    assert resolve(env, "a", "producao")[0]["name"] == "Y" and not resolve(env, "a", "staging")
    assert bound_to(env[0], "a") and not bound_to(env[0], "b")
    assert initials("Maria da Silva") == "MS" and initials("x") == "X" and initials("") == "?"
    assert relative(0, 30_000) == "agora mesmo" and relative(0, 3_600_000) == "há 1 hora"
    assert relative(0, 86_400_000 * 3) == "há 3 dias" and relative(0, 86_400_000 * 40).count("/") == 1
    assert same_roles(["A", " b "], ["B", "a"])
    assert admin_level([{"name": "Gestor", "permissions": [True, True]}], "gestor")
    assert not admin_level([{"name": "Gestor", "permissions": [True, False]}], "Gestor")
    assert api_required("GET", ["admin", "env"]) == ("gerenciar_variaveis", "editar_configuracoes")
    assert api_required("POST", ["admin", "env", "X", "reveal"]) == ("gerenciar_variaveis",)
    assert api_required("GET", ["admin", "users"]) == ("gerenciar_usuarios",)
    assert api_required("GET", ["admin", "connections"]) == ("editar_configuracoes",)
    v = user_view(
        {
            "id": "u",
            "email": "e",
            "name": "n",
            "roles": [],
            "active": True,
            "created_at": 1,
            "password_hash": "h",
        },
        10,
    )
    assert "password_hash" not in v and v["local_password"] is True and next(iter(v)) == "id"


# ---- usuários -----------------------------------------------------------------------------------


def test_users_crud_audit_and_no_hash_leak(hub: TestClient) -> None:
    r = hub.post(
        "/api/admin/users",
        json={"email": "Ana@Demo.local", "name": "Ana", "roles": ["Colaborador"], "password": PASSWORD},
    )
    assert r.status_code == 201
    ana = r.json()
    assert (
        ana["email"] == "ana@demo.local"
        and ana["must_change_password"] is True
        and ana["local_password"] is True
    )
    assert "password" not in json.dumps(ana).lower().replace("local_password", "").replace(
        "must_change_password", ""
    )
    listed = hub.get("/api/admin/users").json()
    assert {u["email"] for u in listed} == {"admin@demo.local", "ana@demo.local"}
    assert "argon2" not in json.dumps(listed)
    # duplicado, função desconhecida, senha fraca, corpo inválido
    base = {"email": "ana@demo.local", "name": "Ana", "roles": ["Colaborador"], "password": PASSWORD}
    assert hub.post("/api/admin/users", json=base).status_code == 400
    assert hub.post(
        "/api/admin/users", json={**base, "email": "b@demo.local", "roles": ["Inexistente"]}
    ).json() == {"error": "Função desconhecida: Inexistente."}
    assert (
        hub.post("/api/admin/users", json={**base, "email": "b@demo.local", "password": "123"}).status_code
        == 400
    )
    assert hub.post("/api/admin/users", json={"email": "x"}).json()["error"].startswith("Dados inválidos")
    assert (
        hub.post(
            "/api/admin/users", content="isto não é json", headers={"content-type": "application/json"}
        ).status_code
        == 400
    )
    assert (
        hub.post("/api/admin/users", content="a=b", headers={"content-type": "text/plain"}).status_code == 415
    )
    # editar, desativar, reativar
    uid = ana["id"]
    r = hub.put(f"/api/admin/users/{uid}", json={"name": "Ana Maria", "roles": ["Tech Lead"]})
    assert (r.json()["name"], r.json()["roles"]) == ("Ana Maria", ["Tech Lead"])
    assert hub.put(f"/api/admin/users/{uid}", json={"active": False}).json()["active"] is False
    assert hub.put(f"/api/admin/users/{uid}", json={"active": True}).json()["active"] is True
    assert hub.put("/api/admin/users/nao-existe", json={"name": "x"}).status_code == 404
    # redefinir senha: provisória, troca obrigatória; nunca a própria
    r = hub.post(f"/api/admin/users/{uid}/reset-password", json={"password": "outra-senha-456"})
    assert r.status_code == 200 and r.json()["must_change_password"] is True
    assert hub.post(f"/api/admin/users/{uid}/reset-password", json={"password": "fraca"}).status_code == 400
    me = next(u["id"] for u in listed if u["email"] == "admin@demo.local")
    assert (
        hub.post(f"/api/admin/users/{me}/reset-password", json={"password": "outra-senha-456"}).status_code
        == 403
    )
    # excluir: só desativado, nunca a própria conta
    assert dele(hub, f"/api/admin/users/{uid}").status_code == 400
    assert dele(hub, f"/api/admin/users/{me}").status_code == 403
    hub.put(f"/api/admin/users/{uid}", json={"active": False})
    assert dele(hub, f"/api/admin/users/{uid}").status_code == 204
    assert dele(hub, f"/api/admin/users/{uid}").status_code == 404
    titles = audit_titles(hub)
    for t in ("Usuário criado", "Usuário alterado", "Senha redefinida", "Usuário excluído"):
        assert t in titles
    assert hub.app.state.admin.chain.verify().ok  # toda escrita entrou na cadeia de auditoria
    details = json.dumps(asyncio.run(hub.app.state.admin.cfg.read())["audit"])  # type: ignore[attr-defined]
    assert PASSWORD not in details and "outra-senha-456" not in details


def test_users_self_protection_last_admin_and_unlock(hub: TestClient) -> None:
    users = hub.get("/api/admin/users").json()
    me = users[0]["id"]
    assert hub.put(f"/api/admin/users/{me}", json={"active": False}).status_code == 403
    assert hub.put(f"/api/admin/users/{me}", json={"roles": ["Colaborador"]}).status_code == 403
    assert hub.put(f"/api/admin/users/{me}", json={"name": "Outro Nome"}).status_code == 200
    other_admin = add_user(hub, "chefe", ["Administrador"])
    # o outro administrador pode desativar o primeiro; ninguém desativa o último ativo
    assert other_admin.put(f"/api/admin/users/{me}", json={"active": False}).status_code == 200
    assert other_admin.put("/api/admin/users/chefe", json={"active": False}).status_code == 403
    # bloqueio por tentativas: o desbloqueio zera contagem e atraso
    hub.app.state.login_failures[f"acct:1.2.3.4|{users[0]['email']}"] = [
        int(__import__("time").time() * 1000)
    ] * 5  # type: ignore[attr-defined]
    assert next(u for u in other_admin.get("/api/admin/users").json() if u["id"] == me)["locked"] is True
    r = other_admin.post(f"/api/admin/users/{me}/unlock", json={})
    assert r.status_code == 200 and r.json()["locked"] is False and not hub.app.state.login_failures  # type: ignore[attr-defined]
    assert other_admin.post("/api/admin/users/nao-existe/unlock", json={}).status_code == 404


def test_users_rbac_denials_and_privilege_escalation(hub: TestClient) -> None:
    colab = add_user(hub, "colab", ["Colaborador"])
    lead = add_user(hub, "lead", ["Tech Lead"])
    for c in (colab, lead):
        assert c.get("/api/admin/users").status_code == 403
        assert c.post("/api/admin/users", json={}).status_code == 403
        assert c.get("/admin/users").status_code == 403
        assert c.get("/api/admin/roles").status_code == 403
        assert c.get("/admin/roles").status_code == 403
    assert colab.get("/api/admin/env").status_code == 403 and colab.get("/admin/env").status_code == 403
    assert colab.get("/admin/connections").status_code == 403
    # função com "Gerenciar usuários": gerencia usuários comuns, mas não escala privilégios
    assert (
        hub.post(
            "/api/admin/roles", json={"name": "RH", "description": "x", "level": "Alto", "members": 0}
        ).status_code
        == 201
    )
    assert hub.put("/api/admin/roles/RH/permissions/1", json={"granted": True}).status_code == 200
    assert hub.put("/api/admin/roles/RH/permissions/0", json={"granted": True}).status_code == 200
    rh = add_user(hub, "rh", ["RH"])
    assert rh.get("/api/admin/users").status_code == 200 and rh.get("/admin/users").status_code == 200
    new = {"email": "n@demo.local", "name": "N", "roles": ["Colaborador"], "password": PASSWORD}
    assert rh.post("/api/admin/users", json=new).status_code == 201
    assert (
        rh.post(
            "/api/admin/users", json={**new, "email": "m@demo.local", "roles": ["Administrador"]}
        ).status_code
        == 403
    )
    assert (
        rh.post("/api/admin/users", json={**new, "email": "m@demo.local", "roles": ["RH"]}).status_code == 403
    )
    assert (
        rh.post("/api/admin/users", json={**new, "email": "m@demo.local", "roles": ["Tech Lead"]}).status_code
        == 403
    )
    admin_id = next(u["id"] for u in rh.get("/api/admin/users").json() if u["email"] == "admin@demo.local")
    assert rh.put(f"/api/admin/users/{admin_id}", json={"name": "x"}).status_code == 403
    assert (
        rh.post(
            f"/api/admin/users/{admin_id}/reset-password", json={"password": "outra-senha-456"}
        ).status_code
        == 403
    )
    # quem só gerencia usuários vê as funções mas não as altera (senão daria a si mesmo qualquer acesso)
    assert rh.get("/api/admin/roles").status_code == 200
    assert rh.post("/api/admin/roles", json={"name": "Y", "level": "Alto"}).status_code == 403
    assert rh.put("/api/admin/roles/RH/permissions/2", json={"granted": True}).status_code == 403
    assert "Só o Administrador" in dele(rh, "/api/admin/roles/RH").json()["error"]
    # token de máquina sem sessão não chega às telas de administração
    assert hub.get("/api/admin/users", headers={"authorization": "Bearer hubt_invalido"}).status_code == 401


# ---- funções ------------------------------------------------------------------------------------


def test_roles_crud_permissions_apps_and_live_effect(hub: TestClient) -> None:
    roles = hub.get("/api/admin/roles").json()
    assert [r["name"] for r in roles] == ["Administrador", "Product Owner", "Tech Lead", "Colaborador"]
    assert list(roles[0]) == ["name", "description", "members", "level", "permissions"]
    r = hub.post(
        "/api/admin/roles", json={"name": "Auditor", "description": " olha ", "level": "Padrão", "members": 2}
    )
    assert (
        r.status_code == 201 and r.json()["permissions"] == [False] * 6 and r.json()["description"] == "olha"
    )
    assert (
        hub.post("/api/admin/roles", json={"name": "auditor", "level": "Padrão"}).status_code == 400
    )  # duplicada
    assert hub.post("/api/admin/roles", json={"name": "", "level": "Padrão"}).status_code == 400
    assert hub.post("/api/admin/roles", json={"name": "Z", "level": "Qualquer"}).status_code == 400
    assert (
        hub.put(
            "/api/admin/roles/Auditor", json={"description": "novo", "level": "Alto", "members": 5}
        ).json()["level"]
        == "Alto"
    )
    assert hub.put("/api/admin/roles/Inexistente", json={"level": "Alto"}).status_code == 404
    # a permissão vale na hora para quem já tem a função
    aud = add_user(hub, "aud", ["Auditor"])
    assert aud.get("/admin/audit").status_code == 403
    assert (
        hub.put("/api/admin/roles/Auditor/permissions/3", json={"granted": True}).json()["permissions"][3]
        is True
    )
    assert aud.get("/admin/audit").status_code != 403
    assert hub.put("/api/admin/roles/Auditor/permissions/9", json={"granted": True}).status_code == 400
    assert hub.put("/api/admin/roles/Auditor/permissions/x", json={"granted": True}).status_code == 400
    assert hub.put("/api/admin/roles/Administrador/permissions/1", json={"granted": False}).status_code == 400
    # aplicações permitidas
    assert hub.put("/api/admin/roles/Auditor/apps", json={"apps": ["ativos", "ativos"]}).json()["apps"] == [
        "ativos"
    ]
    assert hub.put("/api/admin/roles/Auditor/apps", json={"apps": ["nao-existe"]}).status_code == 400
    assert hub.put("/api/admin/roles/Auditor/apps", json={"apps": None}).json().get("apps") is None
    assert hub.put("/api/admin/roles/Administrador/apps", json={"apps": ["ativos"]}).status_code == 400
    # Administrador não se exclui; as demais sim
    assert dele(hub, "/api/admin/roles/Administrador").status_code == 400
    assert dele(hub, "/api/admin/roles/Auditor").status_code == 204
    assert dele(hub, "/api/admin/roles/Auditor").status_code == 404
    titles = audit_titles(hub)
    for t in (
        "Função criada",
        "Função atualizada",
        "Permissões de função alteradas",
        "Aplicações da função alteradas",
        "Função excluída",
    ):
        assert t in titles


def test_roles_page_markup_editable_and_readonly(hub: TestClient) -> None:
    html = hub.get("/admin/users?tab=roles").text
    assert hub.get("/admin/roles").headers["location"] == "/admin/users?tab=roles"
    assert html.count('class="role-card"') == 4 and 'data-admin-add="role"' in html
    assert 'data-role="Administrador"' in html and 'data-role-perm="5"' in html
    assert '"members":4' in html.replace("&#34;", '"') and "1 usuário" in html
    rh_html = add_user(hub, "x1", ["Colaborador"]).get("/admin/users?tab=roles")
    assert rh_html.status_code == 403
    hub.post("/api/admin/roles", json={"name": "RH2", "level": "Alto"})
    hub.put("/api/admin/roles/RH2/permissions/1", json={"granted": True})
    ro = add_user(hub, "rh2", ["RH2"]).get("/admin/users?tab=roles").text
    assert (
        'data-admin-add="role"' not in ro and "role-autosave" not in ro and "Só o Administrador altera" in ro
    )
    assert 'data-role-perm="0"' in ro and "disabled" in ro


# ---- variáveis de ambiente ------------------------------------------------------------------------


def test_env_seed_masking_and_page(hub: TestClient) -> None:
    listed = hub.get("/api/admin/env").json()
    names = [v["name"] for v in listed]
    assert names[:3] == ["API_BASE_URL", "AUTH_CLIENT_ID", "LOG_LEVEL"] and "ASSET_SYNC_INTERVAL_MIN" in names
    secret = next(v for v in listed if v["name"] == "AUTH_CLIENT_ID")
    assert secret["value"] == "••••••••3f9a" and "sk_live" not in json.dumps(listed)
    api = next(v for v in listed if v["name"] == "API_BASE_URL")
    assert [b["app"] for b in api["bindings"]] == ["ativos", "fornecedores", "servicos"] and api[
        "managed"
    ] is False
    html = hub.get("/admin/env").text
    assert "sk_live" not in html and "••••••••3f9a" in html
    assert "data-env-reveal" not in html and "env-icon-placeholder" not in html  # segredo nunca é revelado
    only = hub.get("/admin/env?app=servicos").text
    assert "NOTIFICATIONS_WEBHOOK_URL" in only and "FEATURE_FLAG_NEW_DASHBOARD" not in only
    assert 'data-env-app="servicos"' in only and 'data-env-app="ativos"' not in only
    assert 'data-apps="servicos"' in only and "Provisionar banco" in only
    assert "app=desconhecido" not in hub.get("/admin/env?app=desconhecido").text


def test_env_crud_validation_audit_and_pending_restart(hub: TestClient) -> None:
    body = {
        "name": "MINHA_VAR",
        "value": SECRET,
        "kind": "secreta",
        "bindings": [{"app": "ativos", "env": "producao"}, {"app": "ativos", "env": "producao"}],
    }
    r = hub.post("/api/admin/env", json=body)
    assert r.status_code == 201 and r.json()["value"] == "••••••••3456" and len(r.json()["bindings"]) == 1
    assert SECRET not in r.text
    assert hub.post("/api/admin/env", json=body).status_code == 400  # duplicada
    for bad in (
        {**body, "name": "minuscula"},
        {**body, "name": "OUTRA", "value": ""},
        {**body, "name": "OUTRA", "value": "a\nb"},
        {**body, "name": "OUTRA", "kind": "estranha"},
        {**body, "name": "OUTRA", "bindings": [{"app": "nao", "env": "producao"}]},
        {**body, "name": "OUTRA", "bindings": [{"app": "ativos", "env": "dev"}]},
        {**body, "name": "PORT"},
        {**body, "name": "HUB_INTERNAL_TOKEN"},
        {**body, "name": "HUB_STORAGE_TOKEN_X"},
        {**body, "name": "OUTRA", "inject_as": "HUB_CONNECTIONS"},
        {**body, "name": "OUTRA", "inject_as": "ruim"},
        {
            **body,
            "name": "OUTRA",
            "bindings": [{"app": "ativos", "env": "producao"}],
            "inject_as": "LOG_LEVEL",
        },
    ):
        assert hub.post("/api/admin/env", json=bad).status_code == 400, bad
    # conflito de nome no container para o mesmo app × ambiente
    clash = {
        "name": "OUTRA",
        "value": "x",
        "kind": "publica",
        "bindings": [{"app": "ativos", "env": "producao"}],
        "inject_as": "MINHA_VAR",
    }
    assert "já entra como MINHA_VAR" in hub.post("/api/admin/env", json=clash).json()["error"]
    # editar sem valor mantém o atual (o formulário não recebe segredos)
    r = hub.put(
        "/api/admin/env/MINHA_VAR",
        json={
            "name": "MINHA_VAR",
            "value": "",
            "kind": "secreta",
            "bindings": [{"app": "coletor", "env": "staging"}],
        },
    )
    assert r.status_code == 200 and r.json()["bindings"] == [{"app": "coletor", "env": "staging"}]
    assert hub.post("/api/admin/env/MINHA_VAR/reveal", json={}).status_code in (
        403,
        404,
        405,
    )  # não existe mais
    pend = asyncio.run(hub.app.state.admin.cfg.pending_restart())  # type: ignore[attr-defined]
    assert "MINHA_VAR" in pend["ativos"]["vars"] and "MINHA_VAR" in pend["coletor"]["vars"]
    assert "Reinício pendente" in hub.get("/admin/env").text
    assert hub.put("/api/admin/env/NAO_EXISTE", json={"value": "x", "kind": "publica"}).status_code == 404
    assert dele(hub, "/api/admin/env/MINHA_VAR").status_code == 204
    assert dele(hub, "/api/admin/env/MINHA_VAR").status_code == 404
    audit = asyncio.run(hub.app.state.admin.cfg.read())["audit"]
    assert SECRET not in json.dumps(audit)
    assert not any(e["title"] == "Valor sensível revelado" for e in audit)


def test_env_secrets_are_sealed_on_disk_and_never_logged(
    hub: TestClient, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    hub.post("/api/admin/env", json={"name": "SEGREDO", "value": SECRET, "kind": "secreta", "bindings": []})
    hub.post(
        "/api/admin/env",
        json={
            "name": "CONN",
            "value": "postgres://u:senha-do-banco@h/db",
            "kind": "connection_string",
            "bindings": [],
        },
    )
    raw = (tmp_path / "admin.json").read_text(encoding="utf-8")
    assert SECRET not in raw and "senha-do-banco" not in raw and "sk_live_7f3ac82e9b3f9a" not in raw
    assert "https://api.platform.internal" in raw  # as públicas ficam legíveis
    chain = (tmp_path / "audit" / "chain.jsonl").read_text(encoding="utf-8")
    assert SECRET not in chain and "senha-do-banco" not in chain
    assert SECRET not in caplog.text and "senha-do-banco" not in caplog.text
    for page in ("/admin/env", "/api/admin/env"):
        assert SECRET not in hub.get(page).text and "senha-do-banco" not in hub.get(page).text


def test_env_rbac_and_token_scope(hub: TestClient) -> None:
    lead = add_user(hub, "lead", ["Tech Lead"])  # variáveis + configurações
    po = add_user(hub, "po", ["Product Owner"])  # sem variáveis nem configurações
    assert po.get("/api/admin/env").status_code == 403 and po.get("/admin/env").status_code == 403
    assert lead.get("/api/admin/env").status_code == 200 and lead.get("/admin/env").status_code == 200
    assert (
        lead.post("/api/admin/env", json={"name": "LEAD_VAR", "value": "1", "kind": "publica"}).status_code
        == 201
    )
    assert lead.post("/api/admin/env/AUTH_CLIENT_ID/reveal", json={}).status_code in (403, 404, 405)
    assert hub.post("/api/admin/roles", json={"name": "Leitor Config", "level": "Padrão"}).status_code == 201
    hub.put("/api/admin/roles/Leitor Config/permissions/2", json={"granted": True})  # só editar_configuracoes
    cfg = add_user(hub, "cfg", ["Leitor Config"])
    assert cfg.get("/api/admin/env").status_code == 200  # leitura mascarada
    assert cfg.post("/api/admin/env", json={"name": "X", "value": "1", "kind": "publica"}).status_code == 403
    assert cfg.post("/api/admin/env/AUTH_CLIENT_ID/reveal", json={}).status_code in (403, 404, 405)
    assert dele(cfg, "/api/admin/env/LOG_LEVEL").status_code == 403


def test_managed_variables_are_read_only_and_internal_tokens_live_in_the_vault(
    hub: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = hub.app.state.admin  # type: ignore[attr-defined]
    cfg = store.cfg
    token = "ativos-token-interno-demo-7f3a91"
    created = asyncio.run(
        cfg.ensure_internal_tokens("plataforma", lambda app: token if app == "ativos" else None)
    )
    assert set(created) == {"ativos", "coletor", "fornecedores", "servicos"}
    assert (
        asyncio.run(cfg.ensure_internal_tokens("plataforma", lambda app: token if app == "ativos" else None))
        == []
    )
    assert store.tokens["ativos"] == token and len(store.tokens["servicos"]) == 64
    var = next(v for v in hub.get("/api/admin/env").json() if v["name"] == "HUB_INTERNAL_TOKEN_ATIVOS")
    assert (
        var["managed"] is True and var["inject_as"] == "HUB_INTERNAL_TOKEN" and token not in json.dumps(var)
    )
    assert token not in (tmp_path / "admin.json").read_text(encoding="utf-8")  # cifrado no cofre
    # a busca federada e a Inbox usam o token do cofre (e não o ambiente do hub)
    monkeypatch.setenv("HUB_INTERNAL_TOKEN_ATIVOS", "valor-do-ambiente-que-nao-vale")
    assert srch.internal_token("ativos") == token
    # somente leitura aqui; rotacionar é a ação auditada
    ro = hub.put(
        "/api/admin/env/HUB_INTERNAL_TOKEN_ATIVOS",
        json={"value": "novo-valor-qualquer", "kind": "secreta", "bindings": var["bindings"]},
    )
    assert ro.status_code == 400 and "gerida pela plataforma" in ro.json()["error"]
    assert dele(hub, "/api/admin/env/HUB_INTERNAL_TOKEN_ATIVOS").status_code == 400
    old = store.tokens["servicos"]
    asyncio.run(cfg.rotate_internal_token("plataforma", "servicos"))
    assert store.tokens["servicos"] != old and "Token interno rotacionado" in audit_titles(hub)
    with pytest.raises(Exception, match="sem token interno"):
        asyncio.run(cfg.rotate_internal_token("plataforma", "nao-existe"))
    assert "gerida" in hub.get("/admin/env").text
    # "Rotacionar token" do card do app (POST /api/ops/rotate): troca o token e deixa o reinício pendente
    before = store.tokens["coletor"]
    r = hub.post("/api/ops/rotate/coletor/token", json={})
    assert (
        r.json() == {"variable": "HUB_INTERNAL_TOKEN_COLETOR", "restart_pending": True}
        and store.tokens["coletor"] != before
    )
    assert hub.post("/api/ops/rotate/coletor/database", json={}).status_code == 409  # sem banco provisionado
    assert hub.post("/api/ops/rotate/nao-existe/token", json={}).status_code == 404
    assert hub.post("/api/ops/rotate/coletor/outro", json={}).status_code == 404
    assert (
        add_user(hub, "po2", ["Product Owner"]).post("/api/ops/rotate/coletor/token", json={}).status_code
        == 403
    )
    assert api_required("POST", ["ops", "rotate", "ativos", "token"]) == ("gerenciar_variaveis",)


def test_bootstrap_import_only_creates_missing_without_marking_restart(hub: TestClient) -> None:
    cfg = hub.app.state.admin.cfg  # type: ignore[attr-defined]
    items = [
        {
            "name": "DATABASE_URL_ATIVOS",
            "kind": "connection_string",
            "inject_as": "HUB_DB_URL",
            "value": "postgres://u:p@h/db",
            "bindings": [{"app": "ativos", "env": "producao"}],
        },
        {"name": "LOG_LEVEL", "kind": "publica", "value": "debug", "bindings": []},
    ]
    assert asyncio.run(cfg.import_bootstrap("plataforma", items)) == 1  # LOG_LEVEL já existe
    assert asyncio.run(cfg.import_bootstrap("plataforma", items)) == 0
    assert asyncio.run(cfg.pending_restart()) == {}
    page = hub.get("/admin/env").text
    assert (
        "Verificar banco" in page
        and "Rotacionar senha do banco" in page
        and "HUB_DB_URL (connection string) [P]" in page
    )


def test_users_page_markup(hub: TestClient) -> None:
    add_user(hub, "bia", ["Colaborador"])
    html = hub.get("/admin/users").text
    assert (
        'class="data-table hub-table-cards"' in html
        and 'data-label="Usuário"' in html
        and "hub-card-title" in html
    )
    assert 'data-user-new=""' in html and "/assets/users.js" in html and "2 usuários" in html
    assert html.count("data-user-id=") == 2 and "hub-row-menu-list" in html and "hub-cards-only" in html
    assert "argon2" not in html and "password_hash" not in html
    # a própria conta nunca oferece "Desativar"
    row = html.split('data-user-id="u-test"')[1].split("</tr>")[0]
    assert "data-user-toggle" not in row and "Redefinir senha" in row
    assert "data-user-toggle" in html.split('data-user-id="bia"')[1]
    hub.put("/api/admin/users/bia", json={"active": False})
    assert "Reativar" in hub.get("/admin/users").text and "Desativado" in hub.get("/admin/users").text


def test_bootstrap_ignores_bindings_of_apps_outside_catalog(hub: TestClient) -> None:
    """Instalação sem os apps de exemplo: a carga inicial cita apps que não existem — descarta o vínculo e segue
    (antes o hub não subia: AdminError «Aplicação desconhecida»)."""
    cfg = hub.app.state.admin.cfg  # type: ignore[attr-defined]
    items = [
        {"name": "DB_FANTASMA", "kind": "connection_string", "inject_as": "HUB_DB_URL",
         "value": "postgres://u:p@h/db", "bindings": [{"app": "nao-instalado", "env": "producao"}]},
        {"name": "DB_MISTO", "kind": "connection_string", "inject_as": "HUB_DB_URL",
         "value": "postgres://u:p@h/db2",
         "bindings": [{"app": "nao-instalado", "env": "producao"}, {"app": "ativos", "env": "staging"}]},
    ]  # fmt: skip
    assert asyncio.run(cfg.import_bootstrap("plataforma", items)) == 1  # só a mista entra
    env = asyncio.run(cfg.read())["env"]
    nomes = {e["name"]: e for e in env}
    assert "DB_FANTASMA" not in nomes
    assert nomes["DB_MISTO"]["bindings"] == [{"app": "ativos", "env": "staging"}]
