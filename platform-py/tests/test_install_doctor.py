"""install.sh --doctor: read-only network/engine checks that name the cause and the option that fixes it."""

from __future__ import annotations

from pathlib import Path

import pytest
from install_helper import BASH, RECORDING_DOCKER, prod_env, run_install

pytestmark = pytest.mark.skipif(BASH is None, reason="bash not available")

PEM = "-----BEGIN CERTIFICATE-----\nMIIBfakeCA\n-----END CERTIFICATE-----\n"
PROXY = "http://proxy.empresa.local:8080"

# Fake curl for three networks ($FAKE_NET): direct (open internet), proxy-only (no direct way out) and intercept
# (proxy that inspects TLS: without the corporate CA every https certificate fails, exit 60). Mirrors on
# repo.empresa.local always answer; $FAKE_404 makes the URLs containing it answer 404. Each call goes to $FAKE_LOG.
FAKE_CURL = r"""
url="" proxy="" cacert="" verbose=0
while [ $# -gt 0 ]; do
  case "$1" in
    --proxy) proxy=$2; shift ;;
    --cacert) cacert=$2; shift ;;
    -sv | -v) verbose=1 ;;
    -o | -w | --max-time | --noproxy) shift ;;
    http*) url=$1 ;;
  esac
  shift
done
echo "curl $url proxy=$proxy cacert=$cacert" >> "$FAKE_LOG"
case "$url" in *"${FAKE_404:-@@}"*) printf 404; exit 0 ;; *repo.empresa.local*) printf 200; exit 0 ;; esac
case "$FAKE_NET" in
  direct) printf 200 ;;
  proxy-only) [ -n "$proxy" ] || exit 7; printf 200 ;;
  intercept)
    [ -n "$proxy" ] || exit 7
    if [ -z "$cacert" ]; then [ "$verbose" = 1 ] && echo "*  issuer: CN=Corp Web Gateway" >&2; exit 60; fi
    printf 200 ;;
esac
"""


def doctor(
    tmp_path: Path,
    net: str,
    *args: str,
    env: dict[str, str] | None = None,
    fakes: dict[str, str] | None = None,
) -> tuple[int, str, str]:
    log = tmp_path / "net.log"
    log.touch()
    r = run_install(
        tmp_path,
        ("--doctor", *args),
        fakes={"curl": FAKE_CURL, **(fakes or {})},
        env_extra={"FAKE_NET": net, "FAKE_LOG": str(log), **(env or {})},
    )
    return r.returncode, r.stdout + r.stderr, log.read_text(encoding="utf-8")


def ca(tmp_path: Path) -> str:
    f = tmp_path / "ca-empresa.pem"
    f.write_text(PEM, encoding="utf-8")
    return str(f)


def test_open_internet_passes_and_nothing_is_written(tmp_path: Path) -> None:
    rc, out, _ = doctor(tmp_path, "direct")
    assert rc == 0 and "✘" not in out and "✔" in out and "só de leitura" in out
    assert not (tmp_path / "platform-py" / ".prod.env").exists()


def test_no_direct_way_out_suggests_the_proxy_already_in_the_environment(tmp_path: Path) -> None:
    rc, out, _ = doctor(tmp_path, "proxy-only", env={"HTTPS_PROXY": PROXY})
    assert rc == 1 and f"--proxy {PROXY}" in out


def test_proxy_that_inspects_tls_shows_the_issuer_and_asks_for_the_ca(tmp_path: Path) -> None:
    rc, out, _ = doctor(tmp_path, "intercept", "--proxy", PROXY)
    assert rc == 1 and "Corp Web Gateway" in out and "--ca-file" in out


def test_with_proxy_and_ca_everything_passes(tmp_path: Path) -> None:
    rc, out, _ = doctor(tmp_path, "intercept", "--proxy", PROXY, "--ca-file", ca(tmp_path))
    assert rc == 0 and "✘" not in out, out


def test_with_a_proxy_it_never_tries_to_go_around_it(tmp_path: Path) -> None:
    # a direct attempt on a network with a mandatory proxy shows up in the firewall as a bypass attempt
    _, _, log = doctor(tmp_path, "proxy-only", "--proxy", PROXY)
    calls = [line for line in log.splitlines() if line.startswith("curl ")]
    assert calls and all(f"proxy={PROXY}" in line for line in calls if "repo.empresa.local" not in line)


def test_a_mirror_with_the_wrong_path_is_named_with_its_url(tmp_path: Path) -> None:
    rc, out, _ = doctor(
        tmp_path,
        "direct",
        "--apk-mirror",
        "https://repo.empresa.local/alpine-errado",
        env={"FAKE_404": "alpine-errado"},
    )
    assert rc == 1 and "Alpine" in out and "https://repo.empresa.local/alpine-errado" in out and "404" in out


def test_a_port_in_use_suggests_the_next_free_one(tmp_path: Path) -> None:
    ss = "echo 'LISTEN 0 128 0.0.0.0:8090 0.0.0.0:*'; echo 'LISTEN 0 128 [::]:8091 [::]:*'"
    rc, out, _ = doctor(tmp_path, "direct", fakes={"ss": ss})
    assert rc == 1 and "8090" in out and "--port 8092" in out


def test_build_inside_a_container_gets_the_proxy_and_the_ca(tmp_path: Path) -> None:
    log = tmp_path / "builds.log"
    docker = RECORDING_DOCKER.replace('[ "$1" = compose ] && exit 1 ;;', "exit 0 ;;").replace(
        '"compose version") exit 0 ;;', '"compose version" | "info "*) exit 0 ;;'
    )
    rc, out, _ = doctor(
        tmp_path,
        "intercept",
        "--proxy",
        PROXY,
        "--ca-file",
        ca(tmp_path),
        env={"FAKE_LOG": str(log)},
        fakes={"docker": docker},
    )
    text = log.read_text(encoding="utf-8")
    assert "--build-arg HTTPS_PROXY" in text and "--build-arg EXTRA_CA_PEM" in text and "contêiner" in out
    assert rc == 0, out


def test_the_install_runs_the_doctor_first_and_stops_on_a_failure(tmp_path: Path) -> None:
    log = tmp_path / "net.log"
    log.touch()
    r = run_install(
        tmp_path,
        ("--yes", "--admin-email", "ops@exemplo.com"),
        fakes={"curl": FAKE_CURL, "docker": RECORDING_DOCKER},
        env_extra={"FAKE_NET": "proxy-only", "FAKE_LOG": str(log), "HTTPS_PROXY": PROXY},
    )
    out = r.stdout + r.stderr
    assert r.returncode == 1 and "diagnóstico" in out and "--skip-doctor" in out
    assert "1/5" not in out  # nothing was built
    assert prod_env(tmp_path)  # .prod.env is written before (it holds the answers), the build never started
