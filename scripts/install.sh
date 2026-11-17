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

write_initial_hook_json() {
  local dest="$1" cmd="$2"
  mkdir -p "$(dirname "$dest")"
  cat > "$dest" <<EOF
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "${cmd//\"/\\\"}",
            "timeout": 5
          }
        ]
      }
    ]
  }
}
EOF
  echo "Updated: $dest"
}

merge_hook_node() {
  local dest="$1" cmd="$2"
  DESLOP_FILE="$dest" DESLOP_CMD="$cmd" DESLOP_ACTION="$ACTION" node -e '
    const fs = require("fs");
    const p = process.env.DESLOP_FILE, cmd = process.env.DESLOP_CMD, act = process.env.DESLOP_ACTION;
    const d = JSON.parse(fs.readFileSync(p, "utf8") || "{}");
    d.hooks = d.hooks || {};
    const cur = Array.isArray(d.hooks.UserPromptSubmit) ? d.hooks.UserPromptSubmit : [];
    const next = cur.filter(e => !Array.isArray(e.hooks) || !e.hooks.some(h => String(h.command || "").includes("vcdeslop-hook")));
    if (act === "install") next.push({ matcher: "", hooks: [{ type: "command", command: cmd, timeout: 5 }] });
    if (next.length) d.hooks.UserPromptSubmit = next; else delete d.hooks.UserPromptSubmit;
    if (!Object.keys(d.hooks).length) delete d.hooks;
    fs.writeFileSync(p, JSON.stringify(d, null, 2) + "\n");
  '
}

merge_hook_jq() {
  local dest="$1" cmd="$2" tmp="${1}.tmp.$$"
  if [[ "$ACTION" == "install" ]]; then
    jq --arg cmd "$cmd" '.hooks = (.hooks // {}) | .hooks.UserPromptSubmit = ((.hooks.UserPromptSubmit // [] | map(select((.hooks // [] | any(.command // "" | contains("vcdeslop-hook"))) | not))) + [{matcher:"",hooks:[{type:"command",command:$cmd,timeout:5}]}])' "$dest" > "$tmp"
  else
    jq '.hooks.UserPromptSubmit |= (if . then map(select((.hooks // [] | any(.command // "" | contains("vcdeslop-hook"))) | not)) else [] end) | if (.hooks.UserPromptSubmit | length) == 0 then del(.hooks.UserPromptSubmit) else . end | if (.hooks | length) == 0 then del(.hooks) else . end' "$dest" > "$tmp"
  fi
  mv "$tmp" "$dest"
}

merge_hook_python() {
  local dest="$1" cmd="$2"
  DESLOP_FILE="$dest" DESLOP_CMD="$cmd" DESLOP_ACTION="$ACTION" python3 -c '
import json, os
p, cmd, act = os.environ["DESLOP_FILE"], os.environ["DESLOP_CMD"], os.environ["DESLOP_ACTION"]
with open(p, "r", encoding="utf-8") as fh:
    d = json.load(fh)
hooks = d.setdefault("hooks", {})
cur = [e for e in hooks.get("UserPromptSubmit", []) if not any("vcdeslop-hook" in str(h.get("command", "")) for h in e.get("hooks", []))]
if act == "install":
    cur.append({"matcher": "", "hooks": [{"type": "command", "command": cmd, "timeout": 5}]})
if cur:
    hooks["UserPromptSubmit"] = cur
else:
    hooks.pop("UserPromptSubmit", None)
if not hooks:
    d.pop("hooks", None)
with open(p, "w", encoding="utf-8") as fh:
    json.dump(d, fh, indent=2, ensure_ascii=False); fh.write("\n")
'
}

merge_existing_hook_json() {
  local dest="$1" cmd="$2"
  if command -v node >/dev/null 2>&1; then
    cp "$dest" "$(backup_dest "$dest")"
    merge_hook_node "$dest" "$cmd"
  elif command -v jq >/dev/null 2>&1; then
    cp "$dest" "$(backup_dest "$dest")"
    merge_hook_jq "$dest" "$cmd"
  elif command -v python3 >/dev/null 2>&1; then
    cp "$dest" "$(backup_dest "$dest")"
    merge_hook_python "$dest" "$cmd"
  else
    echo "Warning: existing $dest left untouched (install node, jq, or python3 to merge JSON automatically)." >&2
    return 0
  fi
  echo "Updated: $dest"
}

update_hook_json() {
  local harness="$1" dir="$2" file="hooks.json"
  [[ "$harness" == "claude" ]] && file="settings.json"
  local dest="$dir/$file" cmd="\"$HOOK_BIN\" $harness"
  [[ "$ACTION" == "uninstall" && ! -f "$dest" ]] && return 0
  if [[ "$RUN_MODE" == "dry-run" ]]; then echo "[DRY-RUN] Update hook ($ACTION): $dest"; return 0; fi
  if [[ "$ACTION" == "install" && ! -f "$dest" ]]; then write_initial_hook_json "$dest" "$cmd"; return 0; fi
  merge_existing_hook_json "$dest" "$cmd"
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
      update_hook_json "$target" "$HOME_DIR/.$target"
      update_rule_gate "$target" "$HOME_DIR/.$target"
      ;;
    antigravity) sync_antigravity_plugin ;;
  esac
done
echo "Completed."
