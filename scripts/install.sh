#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SKILL_SRC="$REPO_ROOT/skills/vcdeslop"
CLAUDE_AGENT_SRC="$REPO_ROOT/adapters/claude/agents/vcdeslop-reviewer.md"
HOOK_BIN="$REPO_ROOT/adapters/hooks/vcdeslop-hook"
RULE_SNIPPET="$REPO_ROOT/adapters/rules/global-review-gate.md"
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

require_jq() {
  command -v jq >/dev/null 2>&1 && return 0
  echo "Error: jq is required for $1; install jq before proceeding." >&2
  return 1
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

validate_hook_json() {
  local dest="$1" resolved="$1"
  if [[ -L "$dest" && ! -f "$dest" ]]; then
    echo "Error: dangling or invalid hook JSON symlink: $dest; no changes made." >&2
    return 1
  fi
  [[ -L "$dest" ]] && resolved="$(resolve_hook_path "$dest")"
  [[ -f "$dest" ]] || return 0
  if ! jq -se '
    length == 1 and (.[0] | type == "object" and
      ((.hooks // {}) | type == "object") and
      ((.hooks.UserPromptSubmit // []) | type == "array"))
  ' "$resolved" >/dev/null; then
    echo "Error: invalid hook JSON in $dest; no changes made." >&2
    return 1
  fi
}

preflight_hook_target() {
  local target="$1" dest
  if [[ "$target" == "antigravity" && "$ACTION" == "install" ]]; then
    require_jq "the Antigravity hook"
    return
  fi
  [[ "$target" =~ ^(claude|codex|dsh|cursor)$ ]] || return 0
  dest="$(hook_json_path "$target")"
  [[ -f "$dest" || -L "$dest" || ( "$target" != "cursor" && "$ACTION" == "install" ) ]] || return 0
  require_jq "hook JSON updates"
  validate_hook_json "$dest"
}

preflight_hook_json() {
  local target
  for target in "${TARGETS[@]}"; do
    preflight_hook_target "$target"
  done
}

preflight_hook_json
[[ -f "$HOOK_BIN" && "$RUN_MODE" == "apply" ]] && chmod +x "$HOOK_BIN"

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

merge_hook_json() {
  local dest="$1" cmd="$2" operation="$3" tmp input="/dev/null" write_dest
  write_dest="$(resolve_hook_path "$dest")"
  [[ -f "$dest" ]] && input="$write_dest"
  mkdir -p "$(dirname "$write_dest")"
  tmp="$(mktemp "${write_dest}.tmp.XXXXXX")"
  [[ -f "$dest" ]] && cp -p "$write_dest" "$tmp"
  if ! jq -s --arg cmd "$cmd" --arg operation "$operation" '
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
    if $operation == "install" then
      .hooks = (.hooks // {}) |
      .hooks.UserPromptSubmit = ((.hooks.UserPromptSubmit // []) +
        [{matcher:"",hooks:[{type:"command",command:$cmd,timeout:5}]}])
    elif (($original.hooks.UserPromptSubmit? // []) | length > 0) and
         (.hooks.UserPromptSubmit? | type == "array" and length == 0) then
      del(.hooks.UserPromptSubmit) | if (.hooks | length) == 0 then del(.hooks) else . end
    else . end
  ' < "$input" > "$tmp"; then
    rm -f "$tmp"
    return 1
  fi
  if [[ -f "$dest" ]] && jq -se 'length == 2 and .[0] == .[1]' "$write_dest" "$tmp" >/dev/null; then
    rm -f "$tmp"
    return 0
  fi
  if [[ -f "$dest" ]]; then cp "$write_dest" "$(backup_dest "$dest")"; fi
  mv "$tmp" "$write_dest"
  echo "Updated: $dest"
}

update_hook_json() {
  local harness="$1" dir="$2" dest cmd operation="$ACTION"
  dest="$(hook_json_path "$harness")"
  cmd="\"$HOOK_BIN\" $harness"
  [[ "$harness" == "cursor" ]] && operation="uninstall"
  [[ "$operation" == "uninstall" && ! -f "$dest" ]] && return 0
  if [[ "$RUN_MODE" == "dry-run" ]]; then echo "[DRY-RUN] Update hook ($operation): $dest"; return 0; fi
  merge_hook_json "$dest" "$cmd" "$operation"
}

remove_rule_gate() {
  local harness="$1" dest="$2" stripped="$3"
  [[ ! -f "$dest" ]] && return 0
  local body="$stripped"
  if [[ "$harness" == "cursor" ]]; then
    body="$(printf '%s\n' "$stripped" | sed '/^---$/d; /^description:/d; /^alwaysApply:/d')"
  fi
  if [[ -z "${body//[[:space:]]/}" ]]; then
    remove_entry "$dest"
  else
    write_with_backup "$dest" "$stripped"$'\n'
  fi
}

update_rule_gate() {
  local harness="$1" dir="$2" rel="AGENTS.md"
  [[ "$harness" == "claude" ]] && rel="CLAUDE.md"
  [[ "$harness" == "cursor" ]] && rel="rules/vcdeslop-gate.mdc"
  local dest="$dir/$rel" raw="" stripped=""
  [[ -f "$dest" ]] && raw="$(cat "$dest")"
  stripped="$(printf '%s\n' "$raw" | awk '/<!-- VCDESLOP-AUTO-GATE:START -->/{skip=1;next} /<!-- VCDESLOP-AUTO-GATE:END -->/{skip=0;next} !skip{print}')"
  if [[ "$ACTION" == "uninstall" ]]; then
    remove_rule_gate "$harness" "$dest" "$stripped"
    return 0
  fi
  local block="<!-- VCDESLOP-AUTO-GATE:START -->"$'\n'"$(cat "$RULE_SNIPPET")"$'\n'"<!-- VCDESLOP-AUTO-GATE:END -->"$'\n'
  if [[ "$harness" == "cursor" && -z "${stripped//[[:space:]]/}" ]]; then
    write_with_backup "$dest" $'---\ndescription: VCDeslop code taste & review gate\nalwaysApply: true\n---\n\n'"$block"
  elif [[ -z "${stripped//[[:space:]]/}" ]]; then
    write_with_backup "$dest" "$block"
  else
    write_with_backup "$dest" "${stripped}"$'\n\n'"$block"
  fi
}

sync_antigravity_plugin() {
  local agy_dest="$HOME_DIR/.gemini/config/plugins/vcdeslop"
  if [[ "$ACTION" == "uninstall" ]]; then
    remove_entry "$agy_dest"
    return 0
  fi
  backup_existing "$agy_dest" || return 0
  local pairs=(
    "plugin.json:plugin.json"
    "hooks.json:hooks.json"
    "rules/AGENTS.md:rules/AGENTS.md"
    "adapters/antigravity/agents/vcdeslop-reviewer.md:agents/vcdeslop-reviewer.md"
    "adapters/hooks/vcdeslop-hook:adapters/hooks/vcdeslop-hook"
    "adapters/hooks/vcdeslop-hook.cmd:adapters/hooks/vcdeslop-hook.cmd"
    "adapters/hooks/vcdeslop-hook.ps1:adapters/hooks/vcdeslop-hook.ps1"
  )
  local pair src_rel dst_rel
  for pair in "${pairs[@]}"; do
    src_rel="${pair%%:*}"
    dst_rel="${pair#*:}"
    sync_entry "file" "$REPO_ROOT/$src_rel" "$agy_dest/$dst_rel"
  done
  [[ "$RUN_MODE" == "apply" && -f "$agy_dest/adapters/hooks/vcdeslop-hook" ]] && chmod +x "$agy_dest/adapters/hooks/vcdeslop-hook"
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
      update_hook_json "claude" "$HOME_DIR/.claude"
      update_rule_gate "claude" "$HOME_DIR/.claude"
      ;;
    codex|dsh|cursor)
      sync_entry "dir" "$SKILL_SRC" "$HOME_DIR/.$target/skills/vcdeslop"
      # Cursor uses the rule gate; clean obsolete hooks during install and uninstall.
      update_hook_json "$target" "$HOME_DIR/.$target"
      update_rule_gate "$target" "$HOME_DIR/.$target"
      ;;
    antigravity) sync_antigravity_plugin ;;
  esac
done
echo "Completed."
