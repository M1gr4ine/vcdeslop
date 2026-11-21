# VCDeslop — Vibe Coding Deslop & Code Taste Reviewer

面向 AI 辅助编程（Vibe Coding）的跨 Agent **代码品位审查与去 AI 味（Deslop）** 技能及插件，兼容 **Claude Code**、**OpenAI Codex**、**DeepSeek Harness (`dsh`)**、**Google Antigravity** 与 **Cursor**。

Vibe Coding 最大的问题往往不是代码“跑不通”，而是四类高维护成本的隐形债务：**复制粘贴式膨胀**、**非边界过度防御与静默吞错**、**浅模块透传地狱 / 巨石函数**，以及**只测 Mock 的表演性空壳测试**。本项目把确定性规则、软件设计哲学与双盲独立评审有机结合，在 Review 环节守住人类可维护性底线。

---

## 一、核心机制：同模型双盲审查闭环

每次对话触发 Review / 审查 / PR Diff 检查 / 去 AI 味时，Hook 与规则门禁会引导系统执行四步闭环：

1. **双模式路由**：默认进入只读 **Review Mode**（输出评级、指标看板与最小改动补丁）；明确要求重构清理时进入 **Clean Mode**（先跑通测试锁定行为 -> 做减法手术刀清理 -> 复测验证）。
2. **空白上下文双盲隔离（`spawn`，绝不 `fork`）**：新开一个干净的 Subagent 上下文，不继承实现者的对话历史与“我已自测通过”的说辞，只认实际的 `git diff`、源码、调用链与测试代码。
3. **同模型同等智力（`model: inherit`）**：审阅子代理必须继承主会话的完整模型与推理深度——**严禁**将 Code Review 降级派发给 `haiku`、`flash`、`flash_lite`、`mini` 或 `Explore` 等轻量通道。
4. **确定性静态前置扫描（无需强依赖 Python）**：优先使用目标仓库已配好的原生门禁（如 `eslint`/`jscpd`/`dependency-cruiser`、`ArchUnit`/`PMD`、`sqlfluff`、`golangci-lint`）；若环境装有 Python 3.10+，自动加跑内置零依赖扫描器 `skills/vcdeslop/scripts/preflight_scan.py` 提取客观 `file:line` 指标；无 Python 时直接跳过，不影响主体流程。

---

## 二、三条人类可维护性量化硬红线（`[BLOCKER]`）

任一新增或修改触犯以下三条红线，审查结论直接判定为 `BLOCK`（不设单文件行数或函数复杂度硬阈值，避免迫使模型把高内聚的深模块拆碎）：

### 1. 禁止布尔标志位参数（`Boolean Trap`）& 位置参数 `<= 4`
AI 改需求最喜欢往已有函数末尾塞 `isHardDelete = false`、`skipNotify = false`，导致调用处像猜谜（`deleteOrder(id, true, false)`）且函数内部被劈成 $2^N$ 条交错分支。**三种标准改法**：

```ts
// ❌ 坏品位：调用处无法自解释，多个布尔开关使函数内部逻辑交织
deleteOrder(orderId, true, false);

// ✅ 解法 1（语义分叉）：直接拆成两个职责清晰的函数
purgeOrder(orderId);
archiveOrder(orderId);

// ✅ 解法 2（可选配置）：使用具名 Options / DTO / Record / Python keyword-only `*`
sendNotification(user, "Done", { urgent: true, saveHistory: false });

// ✅ 解法 3（模式/状态）：用 Enum 或字符串字面量联合类型，便于日后扩展第三种状态
setPostVisibility(postId, "public"); // "public" | "private" | "friends_only"
```

- **唯一豁免**：单参数且方法名已把布尔含义说透的 Setter（如 `button.setDisabled(true)`、`modal.setVisible(false)`）。
- 位置参数超过 **4 个** 强制收敛为具名配置对象、`record`、DTO 或结构体。

### 2. 新增重复代码块 `= 0`
- 新增逻辑中不得出现 `>= 10` 行归一化结构高度重合的复制粘贴代码；动手前先检索并复用仓库已有公共逻辑。
- **反教条平衡**：如果两段代码只是“当下长得像”但业务演进方向完全不同，不要用布尔开关把它们硬揉成一个 **Wrong Abstraction（错误抽象）**——可抽取域无关的纯工具原语，或各自保持干净独立。

### 3. 模块循环依赖 `= 0`，严禁跨层引用私有文件
- **零循环依赖**：文件、包或 SQL View 之间不得形成 `A -> B -> ... -> A` 环路。
- 表现层直接导入持久化层时，扫描器给出 `[WARNING]`。审查者依据仓库的分层约定判断是否需要经过 Service。领域层反向依赖表现层或基础设施，以及跨模块访问私有实现，仍为 `[BLOCKER]`。私有路径包括 `/internal/`、`/_internal/`、`/private/`、`/impl/` 或 Python `._private` 内部文件。

---

## 三、代码品位组合拳（Cursor Deslop × Vibe Coding Taste）

| 维度 | 核心审查与清洗项 |
| :--- | :--- |
| **Cursor Deslop 核心** | 删除复述代码意图的废话叙事注释（只保留解释 *why* 与边界不变量的注释）；剥离内部受信任路径的冗余判空与空 `catch {}` / 只打日志吞错块；消灭 `as any`、`@ts-ignore`、裸 `# type: ignore`；拍平无必要的深层嵌套；对齐文件局部命名与风格。 |
| **设计哲学减负** | **Karpathy 极简与手术刀**：拒绝未请求的顺手格式化与单调用点过度抽象；**Ousterhout 深模块**：用窄接口隐藏厚重实现，砍掉 `Controller -> Facade -> Manager -> Helper` 浅透传链；**Grug 凌晨三点准则**：拒绝嵌套三元运算符、炫技高阶链与类型体操，确保值班时 10 秒看懂。 |
| **墓碑与死代码收割** | 抓出 AI 重写时“只写新函数不删旧函数”留下的孤儿导出、死分支与废视图：静态高置信孤儿直接清理；涉及 Spring DI、反射或动态路由无法静态确认时，给出**运行时墓碑探针（Tombstone Probe）**方案先验证零命中再安全拔除。 |
| **心理变异测试打假** | 对 Diff 新增/修改的测试人工注入 4 类变异体（边界 `>=` 改 `>`、条件取反 `!`、删掉返回/落库调用、检查只断言 `toBeDefined` 的假测试），识破“覆盖率很高但业务改错依然全绿”的空壳 Mock 测试。 |

---

## 四、重点支持语言与实战看点

- **Java（Tier 1）**：Spring/MyBatis/JPA 分层守门（按仓库分层约定检查 Controller 注入 Mapper，防止泄漏 `@Entity`）、拒绝单实现 `IXxxService` 空壳接口膨胀、用 `record` 收敛 >4 参数与布尔陷阱、禁止返回 `null` 集合、拦截循环体 N+1 查询；推荐联动 `ArchUnit`、`PMD`、`Pitest`。
- **JavaScript / TypeScript（Tier 1）**：Options 对象替代多布尔开关、杜绝 `as any` 与异步 `try/catch` 吞错、识别 Barrel `index.ts` 循环引用与跨模块 `internal/` 导入、纠正 React `useEffect` 同步派生状态与嵌套 JSX 三元；推荐联动 `jscpd`、`dependency-cruiser`、`knip`、`Stryker`。
- **SQL（Tier 1）**：拦截 `SELECT *`、`SELECT DISTINCT` 掩盖 Join 爆炸、`WHERE DATE(col)` 等非 Sargable 索引杀手、`${}` 原生拼接注入风险，以及外键非空却用 `LEFT JOIN + COALESCE` 掩盖脏数据；将深层嵌套子查询/多重 `CASE` 拆解为命名 `WITH` CTE；推荐联动 `sqlfluff`。
- **AI 高频其他语言（Tier 2）**：**Python**（`*` 强制关键字布尔参数、`tach`/`vulture`/`mutmut`）、**Go**（调用方定义接口、禁止 `_ = err`、`golangci-lint`）、**Rust**（节制 `.unwrap()`/`.clone()`、`clippy`）、**Shell / PowerShell**（禁止 `|| true`/`SilentlyContinue` 静默吞错）。

---

## 五、安装

Windows 使用 PowerShell，macOS 和 Linux 使用 Bash。两种安装器均无需 Python。POSIX 上安装 Claude、Codex、DSH 或 Antigravity 时需要 `jq`，hook 运行时也会用它解析事件。Cursor 使用 `alwaysApply` 规则，仅在清理已有 `hooks.json` 时需要 `jq`。

```powershell
# Windows (PowerShell)：将目标换为 claude | codex | dsh | antigravity | cursor | all
powershell -ExecutionPolicy Bypass -File scripts\install.ps1 claude
```
```bash
# macOS / Linux (Bash)
./scripts/install.sh claude
```

- 目标目录已存在时默认安全跳过，加 `-Force`（PowerShell）或 `--force`（Bash）自动将原目录备份到 `~/.vcdeslop-backups/<时间戳>/` 后替换（避免插件目录内产生重复副本）；加 `-DryRun` / `--dry-run` 仅预览不落盘；加 `-Uninstall` / `--uninstall` 无损移除已安装的 Skill、Hook 项与标记块。
- 仓库自身的结构约束、扩展规则与提交前验证清单见 [AGENTS.md](./AGENTS.md)。

---

## 六、鸣谢与参考（Acknowledgments）

本项目的规则设计、架构品位与工程落地汲取了以下开源项目、开放规范与软件工程思想的启发，特此致谢：

- **Cursor `/deslop` 工作流**：将“清除 AI 叙事废话注释、非边界过度防御判空/吞错与局部风格漂移”提炼为独立后置清理工序的实践范式。
- **Andrej Karpathy & [forrestchang/andrej-karpathy-skills](https://github.com/forrestchang/andrej-karpathy-skills)**：关于 Vibe Coding 常见失控模式的总结，以及 *Simplicity First*（极简优先、拒绝过度设计）与 *Surgical Changes*（手术刀式精准改动）行为准则。
- **John Ousterhout — *A Philosophy of Software Design***：**深模块（Deep Modules）**理论——用窄而简单的接口隐藏内部复杂度，坚决抵制浅层透传 Wrapper 链与教条式拆小文件。
- **Carson Gross — [The Grug Brained Developer](https://grugbrain.dev/)**：对抗“复杂度恶魔（Complexity Demon）”的朴素工程直觉与“凌晨三点值班 10 秒看懂”可读性标准。
- **Shopify Tombstone 模式（[Shopify/tombstone](https://github.com/Shopify/tombstone)）与变异测试（Mutation Testing）**：针对反射/动态路由场景先埋点探针再安全收割死代码的思路，以及用边界/取反/删除变异体识破“高覆盖率假绿 Mock 测试”的方法论。
- **开放规范与静态门禁生态**：[Agent Skills 开放标准（`agentskills.io`）](https://agentskills.io)，以及各语言生态的优秀工具参考——[tach-org/tach](https://github.com/tach-org/tach)、[jendrikseipp/vulture](https://github.com/jendrikseipp/vulture)、[boxed/mutmut](https://github.com/boxed/mutmut)、[kucherenko/jscpd](https://github.com/kucherenko/jscpd)、[sverweij/dependency-cruiser](https://github.com/sverweij/dependency-cruiser)、[webpro-nl/knip](https://github.com/webpro-nl/knip)、[Stryker](https://stryker-mutator.io/)、[ArchUnit](https://www.archunit.org/)、[Pitest](https://pitest.org/)、[sqlfluff](https://github.com/sqlfluff/sqlfluff) 与 [golangci-lint](https://github.com/golangci/golangci-lint)。

