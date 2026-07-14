# 完整 PowerShell Skill 的项目内集成

## 结论

本仓库不再使用任何自制或简化版 `core/`。`.agents/skills/powershell-command-runner/` 已整体替换为用户提供的完整 Skill：

```text
powershell-command-runner/
├─ SKILL.md
├─ .powershell-skills-install.json
├─ agents/openai.yaml
└─ core/
   ├─ execution-contract.md
   ├─ failure-corpus/schema.md
   ├─ pattern-catalog/
   ├─ scripts/
   └─ tests/run-smoke.ps1
```

## 为什么之前的方案不对

原 `SKILL.md` 明确依赖同级 `core/`。我此前根据说明重新制作了一套兼容目录，但这会引入版本偏差，也没有必要。既然已经有完整 ZIP，就应该把 Skill 作为一个不可拆分的单元使用。

## 项目内路径

把完整目录放在：

```text
<repo>/.agents/skills/powershell-command-runner/
```

根目录 `AGENTS.md` 要求所有 Windows/PowerShell 操作优先调用：

```text
$powershell-command-runner
```

如果 Codex UI 没有自动发现该 Skill，`AGENTS.md` 会降级为直接读取同一个 `SKILL.md` 和 `core/`，但不得复制、改写或假装调用了别的 Skill。

## 唯一内容修正

原 ZIP 的 `SKILL.md` 文件开头存在 UTF-8 BOM。某些严格 frontmatter 解析器要求第一个字节就是 `-`，因此本项目副本移除了 BOM。除此之外，Skill 内容保持原样。

## 更新方法

以后收到该 Skill 的新版 ZIP 时：

1. 整体替换 `.agents/skills/powershell-command-runner/`。
2. 再次移除 `SKILL.md` 开头的 BOM（如果存在）。
3. 不要混用新旧 `core` 文件。
4. 查看 Git diff。
5. 在 Windows PowerShell 中运行原始 smoke test：

```powershell
& .\.agents\skills\powershell-command-runner\core\tests\run-smoke.ps1
```

6. 重新启动 Codex 会话并检查 `/skills`。
