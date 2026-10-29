# shellcheck shell=bash
# Where the hub keeps its data (install.sh): engine volumes (default) or folders on a disk the user chooses from the
# list of writable mounts. With --data-dir the databases, the hub data and the MinIO files become host folders
# (labelled :Z under SELinux). --engine-store also moves the images (rootless Podman only; see podman.sh).
DATA_DIR="" ENGINE_STORE="" DISK_TIGHT_GB=20

engine_root() { # the engine's image store, when an engine answers
  podman info --format '{{.Store.GraphRoot}}' 2>/dev/null || docker info --format '{{.DockerRootDir}}' 2>/dev/null || true
}

writable_mounts() { # "mount|free GB|total GB|use%" for the real file systems the user can write to (or that hold HOME)
  df -PTk 2>/dev/null | awk 'NR > 1 && $2 !~ /^(tmpfs|devtmpfs|overlay|squashfs|proc|sysfs|efivarfs|nsfs|fuse.*)$/ {
    printf "%s|%d|%d|%s\n", $7, $5 / 1048576, $3 / 1048576, $6 }' |
    while IFS='|' read -r mnt free total use; do
      if [ -w "$mnt" ] || case "$HOME/" in "${mnt%/}/"*) true ;; *) false ;; esac; then echo "$mnt|$free|$total|$use"; fi
    done
}

mount_of() { # mount_of <path> <mounts>: the longest listed mount that contains the path
  local best="" m
  while IFS='|' read -r m _; do
    case "$1/" in "${m%/}/"*) if [ "${#m}" -gt "${#best}" ]; then best=$m; fi ;; esac
  done <<< "$2"
  printf '%s' "$best"
}

disk_questions() { # interactive: 0 = engine volumes; the disk with more space is recommended when the engine's is tight
  local mounts root eng i=0 best="" bestfree=-1 engfree="" choice=0 m free total use mark
  mounts=$(writable_mounts)
  [ -n "$mounts" ] || return 0
  root=$(engine_root)
  eng=""
  if [ -n "$root" ]; then eng=$(mount_of "$root" "$mounts"); fi
  while IFS='|' read -r m free _; do
    if [ "$free" -gt "$bestfree" ]; then best=$m bestfree=$free; fi
    if [ "$m" = "$eng" ]; then engfree=$free; fi
  done <<< "$mounts"
  echo "Disco para os dados do hub (bancos, arquivos, backups):" >&4
  echo "  0  volumes do engine${eng:+ (disco $eng)}" >&4
  while IFS='|' read -r m free total use; do
    i=$((i + 1)) mark=""
    if [ "$m" = "$eng" ]; then mark=" ← imagens do engine"; fi
    if [ "$m" = "$best" ] && [ -n "$engfree" ] && [ "$engfree" -lt "$DISK_TIGHT_GB" ] && [ "$m" != "$eng" ]; then
      mark="$mark ← recomendado"; choice=$i
    fi
    printf '  %s  %-24s %5s GB livres de %s GB (%s)%s\n' "$i" "$m" "$free" "$total" "$use" "$mark" >&4
  done <<< "$mounts"
  local pick=$choice
  ask pick "Escolha o número ou digite uma pasta" '^[0-9]+$|^/.+'
  case "$pick" in
    0) DATA_DIR="" ;;
    /*) DATA_DIR=$pick ;;
    *) m=$(sed -n "${pick}p" <<< "$mounts" | cut -d'|' -f1)
      [ -n "$m" ] || { echo "opção $pick inexistente: dados nos volumes do engine" >&4; return 0; }
      if [ -w "$m" ]; then DATA_DIR="${m%/}/$PROJECT"; else DATA_DIR="$HOME/$PROJECT-data"; fi ;;
  esac
}

disk_validate() {
  case "$DATA_DIR" in "" | /*) ;; *) echo "--data-dir: '$DATA_DIR' inválido (esperado um caminho absoluto, ex.: /data/app-hub)" >&2; exit 2 ;; esac
  case "$ENGINE_STORE" in "" | /*) ;; *) echo "--engine-store: '$ENGINE_STORE' inválido (esperado um caminho absoluto)" >&2; exit 2 ;; esac
  if [ -n "$ENGINE_STORE" ] && [ "$ROOTLESS" != 1 ]; then
    echo "--engine-store: só para Podman rootless (com Docker, mude o data-root do daemon)" >&2; exit 2
  fi
}

disk_summary() {
  if [ -n "$DATA_DIR" ]; then echo "  • guardar bancos, arquivos e backups em $DATA_DIR"; fi
  if [ -n "$ENGINE_STORE" ]; then echo "  • guardar as imagens num store próprio do hub em $ENGINE_STORE (comandos manuais: source .secrets/hub-env.sh)"; fi
}

disk_write() { # host folders in .prod.env (setv); an existing HUB_OBJSTORE_DIR (advanced option) wins
  [ -n "$DATA_DIR" ] || return 0
  mkdir -p "$DATA_DIR"/app-db "$DATA_DIR"/data "$DATA_DIR"/objstore ||
    { echo "--data-dir: não consegui criar pastas em $DATA_DIR" >&2; exit 2; }
  setv HUB_APPDB_DIR "$DATA_DIR/app-db"
  setv HUB_DATA_HOST_DIR "$DATA_DIR/data"
  grep -q '^HUB_OBJSTORE_DIR=' "$ENVF" || setv HUB_OBJSTORE_DIR "$DATA_DIR/objstore"
  if [ "$(getenforce 2>/dev/null)" = Enforcing ]; then setv HUB_BIND_OPTS ":Z"; fi
}
