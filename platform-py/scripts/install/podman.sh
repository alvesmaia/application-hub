# shellcheck shell=bash
# Rootless Podman for install.sh and hub-start.sh: the API socket the hub drives, with or without a systemd user
# session, and the start after a reboot. Without systemd (or --no-systemd) the hub runs its own socket with cgroupfs
# and no systemd healthcheck timers (DISABLE_HC_SYSTEMD), and an @reboot line in the user's crontab starts it again.
# --engine-store also gives the hub its own image store (own storage.conf and runtime dir), apart from the user's.
SYSTEMD_DIR=${INSTALL_SYSTEMD_DIR:-/run/systemd/system} # tests point it elsewhere
PP_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd) # platform-py of this install
ROOTLESS=0 NOSYSTEMD=${NOSYSTEMD:-0} AUTOSTART=${AUTOSTART:-} ROOTLESS_SOCK="" OWN_SOCKET=0

socket_up() { podman --url "unix://$1" info >/dev/null 2>&1; }

start_own_socket() { # start_own_socket <path>: the hub keeps its own `podman system service`
  socket_up "$1" && return 0
  mkdir -p "$(dirname "$1")" "$HOME/.local/state"
  DISABLE_HC_SYSTEMD=true nohup podman --cgroup-manager=cgroupfs --events-backend=file system service --time=0 \
    "unix://$1" >> "$HOME/.local/state/app-hub-podman.log" 2>&1 &
  local _
  for _ in $(seq 20); do socket_up "$1" && return 0; sleep 1; done
  echo "o socket do Podman ($1) não respondeu; veja $HOME/.local/state/app-hub-podman.log" >&2
  return 1
}

engine_env() { # engine_env <store>: this hub's image store; the user's own Podman (and other projects) stay untouched
  local store=$1 bus="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/bus"
  mkdir -p "$store/storage" "$store/run" && chmod 700 "$store/run"
  if [ ! -f "$store/storage.conf" ]; then
    printf '[storage]\ndriver = "overlay"\ngraphroot = "%s/storage"\nrunroot = "%s/run/storage"\n' "$store" "$store" > "$store/storage.conf"
  fi
  # the user session bus, when there is one, still serves systemd-run (netavark starts aardvark-dns through it)
  if [ -e "$bus" ]; then export DBUS_SESSION_BUS_ADDRESS="unix:path=$bus"; fi
  export XDG_RUNTIME_DIR="$store/run" CONTAINERS_STORAGE_CONF="$store/storage.conf"
}

systemd_session_hint() {
  cat >&2 <<EOT
Este servidor usa systemd, mas a sessão do usuário $(id -un) não está ativa (falta ${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/bus).
Sem ela o Podman não sobe as redes do hub (DNS aardvark) nem os healthchecks. Peça ao administrador, uma vez:
  sudo loginctl enable-linger $(id -un)
  sudo systemctl restart user@$(id -u).service
ou um usuário dedicado ao hub, com linger. (--no-systemd só serve para servidores sem systemd.)
EOT
}

rootless_detect() { # read-only, before the consent: ROOTLESS, OWN_SOCKET and AUTOSTART; stops on a broken user session
  docker --version 2>/dev/null | grep -qi podman || return 0
  [ "$(podman info --format '{{.Host.Security.Rootless}}' 2>/dev/null)" = true ] || return 0
  ROOTLESS=1
  [ -d "$SYSTEMD_DIR" ] || NOSYSTEMD=1
  if [ "$NOSYSTEMD" = 0 ] && [ ! -e "${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/bus" ]; then systemd_session_hint; exit 1; fi
  if [ "$NOSYSTEMD" = 1 ] || [ -n "${ENGINE_STORE:-}" ]; then OWN_SOCKET=1; fi
  if [ -z "$AUTOSTART" ]; then AUTOSTART=$([ "$OWN_SOCKET" = 1 ] && echo cron || echo systemd); fi
  if [ -n "${ENGINE_STORE:-}" ]; then ROOTLESS_SOCK="$ENGINE_STORE/run/podman/podman.sock"
  else ROOTLESS_SOCK="${XDG_RUNTIME_DIR:-/tmp/podman-run-$(id -u)}/podman/podman.sock"; fi
}

rootless_summary() { # lines for the consent screen
  [ "$ROOTLESS" = 1 ] || return 0
  if [ "$OWN_SOCKET" = 1 ]; then echo "  • Podman: o hub roda o próprio socket ($ROOTLESS_SOCK), com cgroupfs"; fi
  case "$AUTOSTART" in
    cron) echo "  • iniciar no boot: uma linha @reboot no crontab do usuário (scripts/hub-start.sh)" ;;
    systemd) echo "  • iniciar no boot: systemctl --user enable podman.socket podman-restart.service" ;;
  esac
}

autostart_cron() { # idempotent: one @reboot line per install folder
  local start="$PP_DIR/scripts/hub-start.sh"
  crontab -l 2>/dev/null | grep -qF "$start" && return 0
  { crontab -l 2>/dev/null || true; echo "@reboot bash $start >> $PP_DIR/.secrets/hub-start.log 2>&1"; } | crontab -
}

write_hub_env() { # .secrets/hub-env.sh: the same engine for manual docker/podman commands
  mkdir -p "$PP_DIR/.secrets"
  {
    echo "# Engine of this hub for manual commands:  source $PP_DIR/.secrets/hub-env.sh"
    echo "export DOCKER_HOST=$DOCKER_HOST DISABLE_HC_SYSTEMD=true"
    if [ -n "${ENGINE_STORE:-}" ]; then
      echo "export XDG_RUNTIME_DIR=$XDG_RUNTIME_DIR CONTAINERS_STORAGE_CONF=$CONTAINERS_STORAGE_CONF"
      if [ -n "${DBUS_SESSION_BUS_ADDRESS:-}" ]; then echo "export DBUS_SESSION_BUS_ADDRESS=$DBUS_SESSION_BUS_ADDRESS"; fi
    fi
    echo "alias podman='podman --cgroup-manager=cgroupfs --events-backend=file'"
  } > "$PP_DIR/.secrets/hub-env.sh"
}

rootless_setup() { # after the consent: socket up, start at boot; exports what the install's commands need
  [ "$ROOTLESS" = 1 ] || return 0
  if [ -n "${ENGINE_STORE:-}" ]; then engine_env "$ENGINE_STORE"; fi
  if [ "$OWN_SOCKET" = 1 ]; then
    start_own_socket "$ROOTLESS_SOCK"
    export DISABLE_HC_SYSTEMD=true
  elif [ "$AUTOSTART" = systemd ]; then
    systemctl --user enable --now podman.socket podman-restart.service >/dev/null 2>&1 || true
  fi
  socket_up "$ROOTLESS_SOCK" ||
    { echo "Podman rootless sem socket em $ROOTLESS_SOCK: rode  systemctl --user enable --now podman.socket" >&2; exit 1; }
  export DOCKER_HOST="unix://$ROOTLESS_SOCK"
  if [ "$OWN_SOCKET" = 1 ]; then write_hub_env; fi
  if [ "$AUTOSTART" = cron ]; then autostart_cron; fi
  loginctl show-user "$(id -un)" -p Linger 2>/dev/null | grep -q yes || [ "$NOSYSTEMD" = 1 ] ||
    echo "aviso: sem linger o hub cai no logout — peça à TI: loginctl enable-linger $(id -un)" >&2
  grep -qw cpu "/sys/fs/cgroup/user.slice/user-$(id -u).slice/user@$(id -u).service/cgroup.controllers" 2>/dev/null ||
    echo "aviso: controlador cpu não delegado ao usuário; os limites de CPU dos apps não serão aplicados (TI: Delegate=cpu cpuset io memory pids em user@.service, docs/IMPLANTACAO.md)" >&2
}
