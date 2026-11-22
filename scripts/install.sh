#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SKILL_SRC="$REPO_ROOT/skills/vcdeslop"
CLAUDE_AGENT_SRC="$REPO_ROOT/adapters/claude/agents/vcdeslop-reviewer.md"
AGY_AGENT_SRC="$REPO_ROOT/adapters/antigravity/agents/vcdeslop-reviewer.md"
STAMP="$(date +%Y%m%d%H%M%S)"

HOME_DIR="$HOME"
BACKUP_ROOT="$HOME_DIR/.vcdeslop-backups/$STAMP"
MODE="link"
ACTION="install"
RUN_MODE="apply"
BACKUP_POLICY="keep"
UNIVERSAL_DONE="no"
TARGETS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    all|claude|codex|dsh|antigravity|cursor|universal) TARGETS+=("$1"); shift ;;
    --home-dir) HOME_DIR="$2"; BACKUP_ROOT="$HOME_DIR/.vcdeslop-backups/$STAMP"; shift 2 ;;
    --copy) MODE="copy"; shift ;;
    --force) BACKUP_POLICY="replace"; shift ;;
    --dry-run) RUN_MODE="dry-run"; shift ;;
    --uninstall) ACTION="uninstall"; shift ;;
    -h|--help) echo "Usage: ./scripts/install.sh [all|claude|codex|dsh|antigravity|cursor|universal] [--home-dir DIR] [--copy] [--force] [--dry-run] [--uninstall]"; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; exit 1 ;;
  esac
done

if [[ ${#TARGETS[@]} -eq 0 ]] || printf '%s\n' "${TARGETS[@]}" | grep -qx "all"; then
  TARGETS=("universal" "claude" "codex" "dsh" "antigravity" "cursor")
fi

hook_json_path() {
  local target="$1" file="hooks.json"
  [[ "$target" == "claude" ]] && file="settings.json"
  printf '%s/.%s/%s' "$HOME_DIR" "$target" "$file"
}

resolve_hook_path() {
  local path="$1" link hops=0
  while [[ -L "$path" ]]; do
    ((hops += 1))
    if (( hops > 40 )); then
      echo "Error: too many hook JSON symlinks: $1" >&2
      return 1
    fi
    link="$(readlink "$path")" || return 1
    if [[ "$link" == /* ]]; then path="$link"; else path="$(dirname "$path")/$link"; fi
  done
  printf '%s' "$path"
}

backup_dest() {
  local orig="$1" rel tag dest
  if [[ "$orig" == "$HOME_DIR/"* ]]; then
    rel="${orig#"$HOME_DIR"/}"
  else
    rel="$(basename "$(dirname "$orig")")_$(basename "$orig")"
  fi
  tag="$(printf '%s' "$rel" | sed 's/^\.*//; s/[\/[:space:].]/_/g; s/^_//')"
  dest="$BACKUP_ROOT/$tag"
  if [[ "$RUN_MODE" == "apply" ]]; then
    mkdir -p "$BACKUP_ROOT"
    [[ -e "$dest" || -L "$dest" ]] && rm -rf "$dest"
  fi
  printf '%s' "$dest"
}

copy_clean_dir() {
  local src="$1" dest="$2"
  mkdir -p "$dest"
  cp -R "$src"/. "$dest"/
  find "$dest" \( -name '__pycache__' -o -name '.git' -o -name '.pytest_cache' -o -name '*.pyc' -o -name '*.pyo' \) -exec rm -rf {} +
}

remove_entry() {
  local dest="$1"
  [[ ! -e "$dest" && ! -L "$dest" ]] && return 0
  if [[ "$RUN_MODE" == "dry-run" ]]; then
    echo "[DRY-RUN] Remove: $dest"
    return 0
  fi
  rm -rf "$dest"
  echo "Removed: $dest"
}

backup_existing() {
  local dest="$1" backup
  [[ ! -e "$dest" && ! -L "$dest" ]] && return 0
  if [[ "$BACKUP_POLICY" != "replace" ]]; then
    echo "Skipping existing target (re-run with --force to back up and replace): $dest" >&2
    return 1
  fi
  backup="$(backup_dest "$dest")"
  if [[ "$RUN_MODE" == "dry-run" ]]; then
    echo "[DRY-RUN] Backup: $dest -> $backup"
    return 0
  fi
  mv "$dest" "$backup"
  echo "Backed up: $dest -> $backup"
}

install_entry() {
  local kind="$1" src="$2" dest="$3"
  if [[ "$RUN_MODE" == "dry-run" ]]; then
    echo "[DRY-RUN] $MODE $kind: $dest -> $src"
    return 0
  fi
  mkdir -p "$(dirname "$dest")"
  if [[ "$kind" == "file" ]]; then cp "$src" "$dest"
  elif [[ "$MODE" == "link" ]]; then ln -s "$src" "$dest"
  else copy_clean_dir "$src" "$dest"
  fi
  echo "Installed ($MODE): $dest -> $src"
}

sync_entry() {
  local kind="$1" src="$2" dest="$3"
  if [[ "$ACTION" == "uninstall" ]]; then
    remove_entry "$dest"
    return 0
  fi
  backup_existing "$dest" || return 0
  install_entry "$kind" "$src" "$dest"
}

write_with_backup() {
  local dest="$1" content="$2"
  if [[ "$RUN_MODE" == "dry-run" ]]; then echo "[DRY-RUN] Write: $dest"; return 0; fi
  mkdir -p "$(dirname "$dest")"
  [[ -f "$dest" ]] && cp "$dest" "$(backup_dest "$dest")"
  printf '%s' "$content" > "$dest"
  echo "Updated: $dest"
}

remove_legacy_hook_json() {
  local harness="$1" dest write_dest tmp
  dest="$(hook_json_path "$harness")"
  [[ -f "$dest" ]] || return 0
  grep -q 'vcdeslop-hook' "$dest" || return 0
  if ! command -v jq >/dev/null 2>&1; then
    echo "Warning: jq not found; please remove legacy vcdeslop-hook entries from $dest manually." >&2
    return 0
  fi
  if [[ "$RUN_MODE" == "dry-run" ]]; then echo "[DRY-RUN] Clean legacy hook: $dest"; return 0; fi
  write_dest="$(resolve_hook_path "$dest")"
  tmp="$(mktemp "${write_dest}.tmp.XXXXXX")"
  cp -p "$write_dest" "$tmp"
  if ! jq -s '
    def script_path:
      "(?:\u0022(?:[^\u0022]*[/\\\\])?vcdeslop-hook(?:\\.ps1|\\.cmd)?\u0022|" +
      "\u0027(?:[^\u0027]*[/\\\\])?vcdeslop-hook(?:\\.ps1|\\.cmd)?\u0027|" +
      "(?:[^[:space:]\u0022\u0027]*[/\\\\])?vcdeslop-hook(?:\\.ps1|\\.cmd)?)";
    def owned:
      type == "object" and
      ((.command // "" | if type == "string" then . else "" end) |
        test("^[[:space:]]*(?:(?:powershell|pwsh)(?:\\.exe)?[[:space:]]+(?:[^[:space:]]+[[:space:]]+)*-file[[:space:]]+)?"
          + script_path + "[[:space:]]+(?:claude|codex|dsh|cursor)[[:space:]]*$"; "i"));
    def cleaned:
      map(if type == "object" and (.hooks | type == "array") then
        .hooks as $old | .hooks |= map(select(owned | not)) |
        if ($old | any(owned)) and (.hooks | length == 0) then empty else . end
      else . end);
    (if length == 0 then {} else .[0] end) | . as $original |
    if .hooks.UserPromptSubmit != null then .hooks.UserPromptSubmit |= cleaned else . end |
    if (($original.hooks.UserPromptSubmit? // []) | length > 0) and
       (.hooks.UserPromptSubmit? | type == "array" and length == 0) then
      del(.hooks.UserPromptSubmit) | if (.hooks | length) == 0 then del(.hooks) else . end
    else . end
  ' < "$write_dest" > "$tmp"; then
    rm -f "$tmp"
    return 1
  fi
  if jq -se 'length == 2 and .[0] == .[1]' "$write_dest" "$tmp" >/dev/null; then
    rm -f "$tmp"
    return 0
  fi
  cp "$write_dest" "$(backup_dest "$dest")"
  mv "$tmp" "$write_dest"
  echo "Updated: $dest"
}

remove_legacy_rule_gate() {
  local harness="$1" dir="$2" rel="AGENTS.md"
  [[ "$harness" == "claude" ]] && rel="CLAUDE.md"
  [[ "$harness" == "cursor" ]] && rel="rules/vcdeslop-gate.mdc"
  local dest="$dir/$rel" raw stripped body
  [[ -f "$dest" ]] || return 0
  raw="$(cat "$dest")"
  grep -q 'VCDESLOP-AUTO-GATE:START' <<< "$raw" || return 0
  stripped="$(printf '%s\n' "$raw" | awk '/<!-- VCDESLOP-AUTO-GATE:START -->/{skip=1;next} /<!-- VCDESLOP-AUTO-GATE:END -->/{skip=0;next} !skip{print}')"
  body="$stripped"
  if [[ "$harness" == "cursor" ]]; then
    body="$(printf '%s\n' "$stripped" | sed '/^---$/d; /^description:/d; /^alwaysApply:/d')"
  fi
  if [[ -z "${body//[[:space:]]/}" ]]; then
    remove_entry "$dest"
  else
    write_with_backup "$dest" "$stripped"$'\n'
  fi
}

sync_antigravity_plugin() {
  local agy_dest="$HOME_DIR/.gemini/config/plugins/vcdeslop"
  if [[ "$ACTION" == "uninstall" ]]; then
    remove_entry "$agy_dest"
    return 0
  fi
  backup_existing "$agy_dest" || return 0
  sync_entry "file" "$REPO_ROOT/plugin.json" "$agy_dest/plugin.json"
  sync_entry "file" "$AGY_AGENT_SRC" "$agy_dest/agents/vcdeslop-reviewer.md"
  sync_entry "dir" "$SKILL_SRC" "$agy_dest/skills/vcdeslop"
}

for target in "${TARGETS[@]}"; do
  if [[ "$target" =~ ^(universal|codex|dsh|cursor)$ && "$UNIVERSAL_DONE" == "no" ]]; then
    if [[ "$target" == "universal" || "$ACTION" == "install" ]]; then
      UNIVERSAL_DONE="yes"
      sync_entry "dir" "$SKILL_SRC" "$HOME_DIR/.agents/skills/vcdeslop"
    fi
  fi
  case "$target" in
    claude)
      sync_entry "dir" "$SKILL_SRC" "$HOME_DIR/.claude/skills/vcdeslop"
      sync_entry "file" "$CLAUDE_AGENT_SRC" "$HOME_DIR/.claude/agents/vcdeslop-reviewer.md"
      remove_legacy_hook_json "claude"
      remove_legacy_rule_gate "claude" "$HOME_DIR/.claude"
      ;;
    codex|dsh|cursor)
      sync_entry "dir" "$SKILL_SRC" "$HOME_DIR/.$target/skills/vcdeslop"
      remove_legacy_hook_json "$target"
      remove_legacy_rule_gate "$target" "$HOME_DIR/.$target"
      ;;
    antigravity) sync_antigravity_plugin ;;
  esac
done
echo "Completed."
