# TODOs（二期 Roadmap）

v0.14.0 未纳入、规划中的增强项，按优先级排列：

## 本迭代追加（已实现，随 v0.14.0 发布）
- [x] 执行中补充指令：任务运行时用户输入进入队列（chat:queued），Agent 在下一回合开始前注入对话（`[用户补充指令]` 前缀），前端消息带"已入队"标记
- [x] MCP Server 改名：设置面板内联重命名，保存后全量替换重连（工具前缀 `mcp_<name>_` 随之更新）
- [x] TODO 清单工具：新增 `todo_write` 工具（全量覆盖式维护，事件 `todo:updated` 实时推送），右栏新增 TODOs 页签（上下文与 MCP 之间），Plan/Build 均可用
- [x] `/compact` 命令：手动触发一次会话压缩（POST /api/compact，策略 B：旧轮次 LLM 摘要化 + 最近 N 轮原样保留，立即落盘）
  - 可带参数定制摘要侧重：`/compact <关注点>`（focus 注入摘要指令）
  - 完成后前端用压缩后的历史替换本地消息、刷新 context/stats 面板水位（compression_count/last_prompt_tokens 回写）
  - 任务运行中拒绝（409）：运行中任务的内存历史会覆盖压缩结果
  - 命令面板已收录 `/compact [关注点]`

## 命令系统
- [ ] OpenCode 式自定义命令文件：`.agents/commands/*.md`（项目）/ `~/.agents/commands/*.md`（全局），文件名即命令名
  - frontmatter：`description` / `agent` / `model` / `subtask`
  - 模板占位符：`$ARGUMENTS`（整段）、`$1 $2`（位置参数）、`` !`shell命令` ``（输出注入）、`@file`（文件内容注入）

## Skills
- [x] Skill 权限控制：`allow / deny / ask` 通配规则（如 `internal-*: deny`），deny 的技能对 Agent 隐藏（对齐 OpenCode `permission.skill`）
  - config.json `skill_permissions`（glob → 动作，插入序首个命中生效，默认 allow）
  - deny：技能索引不列出、triggers 不匹配、load_skill 拦截（`[Skill Denied]`）、/skill 显式加载提示禁用、命令面板隐藏
  - ask：load_skill 走 beforeTool 管道弹审批卡；/skill 与 triggers 在任务启动前逐个审批，拒绝不注入
  - 设置 → 技能页：权限规则编辑器（保存走 /api/config）+ 每技能徽标（🚫 禁用 / ❓ 需确认）
- [ ] `triggers` 高级匹配语法：词边界、正则（当前为大小写不敏感子串匹配）
- [ ] Skill 管理端补充 zip 上传大小上限配置
- [x] TODO 清单持久化：跟随会话落盘（`<config_dir>/todo_boards/<session>.json`，原子写入），页面刷新/服务重启后经 `GET /api/todos` 恢复；会话删除时清理看板；前端切会话时并行拉取还原

## 子 Agent
- [ ] 子 Agent 内部审批透传到父界面（refactor 等角色的高危操作目前走 auto_approve 配置）
- [ ] 子 Agent 流式文本实时显示（当前只显示工具步骤 + 轮次）
- [ ] 子 Agent 卡片归档持久化：跨页面刷新保留（当前归档为会话级内存态）

## 工具并行执行
- [ ] 写类工具精细并行化：同一轮内只串行化写类工具本身，只读工具与其并行（当前含写类整轮串行，偏保守）
