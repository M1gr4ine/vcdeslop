# VCDeslop — Built-in Prompt for Review Agents

面向 AI 编程场景的**纯提示词（Prompt-only）Review Agent 内置指引**。

## 为什么重构为 v3（不做程序限制）

在真实项目实践中，把代码品位做成全局常驻规则、正则 Hook 与机械静态红线会带来明显副作用：

- **编码期自我束缚**：硬性数字指标（如参数个数上限、行数/复杂度上限、零重复阻断）会诱导模型在写代码时过度拆碎高内聚逻辑、做无意义的参数打包或强行抽取错误抽象。
- **噪音与盲区倒挂**：启发式正则扫描器往往在现代语法上产生大量误报，同时对 HTML 内联脚本、跨模块分层倒置以及真实的状态/并发 Bug 完全盲视。
- **流程过重**：全局常驻门禁和宽泛触发的 Hook 会打断正常开发与调试节奏。

因此，**v3 彻底移除了所有程序化限制、静态扫描器、自动触发 Hook 与全局常驻编码规则**——让模型在写代码时拥有充分自由度，只在明确需要 Review 时，把这套精炼的工程判断内置给独立的 Review Agent。

---

## 核心文件

| 文件 | 用途 |
| :--- | :--- |
| [prompts/review-agent.md](./prompts/review-agent.md) | **标准内置系统提示词**（无 Frontmatter，直接嵌入任意 Review Agent、Swarm Reviewer 或自定义工作流） |
| [adapters/claude/agents/vcdeslop-reviewer.md](./adapters/claude/agents/vcdeslop-reviewer.md) | Claude Code 原生 Subagent 预设（`model: inherit`，已内联完整提示词） |
| [adapters/antigravity/agents/vcdeslop-reviewer.md](./adapters/antigravity/agents/vcdeslop-reviewer.md) | Google Antigravity 原生 Subagent 预设（`subagent: true`，已内联完整提示词） |
| [skills/vcdeslop/SKILL.md](./skills/vcdeslop/SKILL.md) | 可选按需调度 Skill（引导主 Agent 用同等模型、空白上下文派发审查） |
| [skills/vcdeslop/references/language-tastes.md](./skills/vcdeslop/references/language-tastes.md) | 可选多语言品位速查（Java / JS / TS / SQL / Python / Go / Rust / Shell，纯启发式，非硬规则） |

---

## 提示词核心关注点（按真实杠杆排序）

1. **正确性、状态不变量与并发安全（最高优先级）**：真实逻辑缺陷、回退陷阱（如 `||` 误吞空串/0）、静默丢弃的配置/环境变量、并发读改写竞态、兄弟适配器一致性。
2. **架构边界与模块深度**：循环依赖、分层倒置、Ousterhout 深模块 vs 浅层透传链——同时明确反对为了形式指标把内聚函数拆碎，反对制造错误抽象（Wrong Abstraction）。
3. **代码品位与去 AI 味（Deslop）**：调用点可读性（避免猜谜式布尔开关，但不苛责合理局部写法）、保留高价值的 *why* / 并发不变量注释而清理复述语法的废话、区分“静默吞错”与“合理边界防御”、Karpathy 手术刀式最小改动。
4. **墓碑与死代码意识**：清理重写后遗留的孤儿路径；静态无法确认动态调用时建议运行时验证，而非盲删。
5. **测试实质（心理变异审视）**：通过假想翻转分支/边界/副作用，识别只测 Mock 编排、与真实 DOM/契约脱节或过大的测试。

---

## 使用方式

### 方式 1：直接作为 Review Agent 的内置提示词（推荐）

直接读取或复制 [prompts/review-agent.md](./prompts/review-agent.md) 作为你的 Review Agent 系统提示词（System Prompt），再把仓库路径与待审查的 `git diff` / 文件范围作为任务输入交给它即可。

建议：
- 让 Review Agent 在**空白上下文**中独立读代码，不附带实现者的自我总结；
- 使用与主会话**同等推理能力**的模型，不降级到轻量模型。

### 方式 2：可选安装为本机 Subagent / Skill（不注入任何 Hook 或全局规则）

安装器**仅安装** `vcdeslop-reviewer` 子代理与按需 `vcdeslop` Skill，**绝不写入** `hooks.json` / `settings.json` Hook，也**绝不修改** `CLAUDE.md` / `AGENTS.md` / Cursor `.mdc` 全局规则。

```powershell
# Windows (PowerShell)：目标可选 claude | codex | dsh | antigravity | cursor | universal | all
powershell -ExecutionPolicy Bypass -File scripts\install.ps1 claude

# 卸载（同时自动清理旧版 v2 遗留的 Hook 与全局规则块）
powershell -ExecutionPolicy Bypass -File scripts\install.ps1 all -Uninstall
```

```bash
# macOS / Linux (Bash)
./scripts/install.sh claude
./scripts/install.sh all --uninstall
```

---

## 鸣谢与思想来源

本提示词的工程品位借鉴了 Cursor `/deslop`、Andrej Karpathy（*Simplicity First* & *Surgical Changes*）、John Ousterhout（*A Philosophy of Software Design*）、Carson Gross（*The Grug Brained Developer*）、Shopify Tombstone 模式以及变异测试（Mutation Testing）的核心思想。
