import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import type { AgentInfo, CommandInfo, LLMConfig, LLMProviderMeta, SessionModel, SkillInfo } from "../types";

export default function Composer({
  disabled = false,
  running,
  agents,
  currentAgent,
  onSelectAgent,
  onSend,
  onStop,
  llmConfig,
  providerMeta,
  sessionModel,
  onSessionModelChange,
  reasoningEffort = "",
  onReasoningEffortChange,
}: {
  disabled?: boolean;
  running: boolean;
  agents: AgentInfo[];
  currentAgent: string;
  onSelectAgent: (id: string) => void;
  onSend: (prompt: string) => void;
  onStop: () => void;
  kind?: never;
  llmConfig: LLMConfig | null;
  providerMeta?: LLMProviderMeta[];
  sessionModel: SessionModel | null;
  onSessionModelChange: (model: SessionModel | null) => void;
  reasoningEffort?: string;
  onReasoningEffortChange?: (v: string) => void;
}) {
  const [text, setText] = useState("");
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [commands, setCommands] = useState<CommandInfo[]>([]);
  const [skills, setSkills] = useState<SkillInfo[]>([]);
  const [selIdx, setSelIdx] = useState(0);
  const [reasoningOpen, setReasoningOpen] = useState(false);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  // 推理强度中文标签
  const reasoningLabel = (v: string) => {
    switch (v) {
      case "low": return "低";
      case "medium": return "中";
      case "high": return "高";
      case "max": return "最大";
      default: return "关";
    }
  };

  // ------------------------------------------------------------ 生效值计算
  // 模型下拉显示「当前对话实际生效的模型/effort」：
  // 会话级 override 优先，否则回退到全局 provider 配置（与后端 create_loop 语义一致）
  const allProviders = providerMeta && providerMeta.length > 0
    ? providerMeta
    : (llmConfig ? Object.entries(llmConfig.providers).map(([id, provider]) => ({
        id, name: provider.name || id, models: provider.models, has_key: provider.has_key,
        kind: "openai" as const, default_base_url: "", model: provider.model,
      })) : []);
  const effProviderId = sessionModel?.provider ?? llmConfig?.active ?? "";
  const effProviderMeta = allProviders.find((p) => p.id === effProviderId);
  const effProviderName = effProviderMeta?.name || effProviderId;
  const effProviderCfg = llmConfig?.providers?.[effProviderId];
  const globalModel = effProviderCfg?.model ?? "";
  // provider 级默认 effort（设置弹窗配的，config.json providers[pid].reasoning_effort）
  const providerEffort = effProviderCfg?.reasoning_effort ?? "";
  // 会话级 override：""=跟随 provider 默认，"off"=显式关闭，档位=具体值
  const hasEffortOverride = reasoningEffort !== undefined && reasoningEffort !== "";
  const displayEffort = hasEffortOverride ? reasoningEffort : (providerEffort || "off");
  // 生效模型：override 或全局 provider 默认
  const effModel = sessionModel?.model ?? globalModel;
  const effSelectValue = `${effProviderId}\n${effModel}`;
  const globalSelectValue = `${llmConfig?.active ?? ""}\n${globalModel}`;
  const isDefaultModel = !sessionModel;

  // 打开面板时懒加载命令与技能列表
  useEffect(() => {
    if (!paletteOpen) return;
    let cancelled = false;
    void (async () => {
      try {
        const [cmdResp, skillResp] = await Promise.all([
          api.commands().catch(() => ({ commands: [] })),
          api.skills().catch(() => ({ skills: [] })),
        ]);
        if (!cancelled) {
          setCommands(cmdResp.commands);
          setSkills(skillResp.skills);
        }
      } catch {
        /* 面板数据加载失败不阻断输入 */
      }
    })();
    return () => { cancelled = true; };
  }, [paletteOpen]);

  // 面板打开时解析当前输入
  const input = text;
  const startsWithSlash = input.startsWith("/");
  const panelVisible = paletteOpen && startsWithSlash && !running;
  const tokens = startsWithSlash ? input.slice(1).split(/\s+/) : [];
  const cmdToken = tokens[0] ?? "";
  const restAfterCmd = input.slice(1 + cmdToken.length).replace(/^\s+/, "");
  const pickingSkill = cmdToken.toLowerCase() === "skill" && !restAfterCmd.includes(" ");

  const filtered = useMemo(() => {
    const q = cmdToken.toLowerCase();
    return commands.filter((c) => c.name.toLowerCase().startsWith(q));
  }, [commands, cmdToken]);

  const filteredSkills = useMemo(() => {
    if (!pickingSkill) return [];
    const q = restAfterCmd.toLowerCase();
    return skills.filter((s) => s.name.toLowerCase().includes(q));
  }, [skills, pickingSkill, restAfterCmd]);

  const candidates: { name: string; description: string; hint: string }[] = pickingSkill
    ? filteredSkills.map((s) => ({ name: s.name, description: s.description || "技能", hint: "skill" }))
    : filtered.map((c) => ({ name: c.name, description: c.description, hint: c.argsHint }));

  // 输入变化时重置选中索引
  useEffect(() => { setSelIdx(0); }, [input]);

  const applySuggestion = (name: string) => {
    if (pickingSkill) {
      setText(`/skill ${name} `);
    } else {
      setText(`/${name} `);
    }
    inputRef.current?.focus();
  };

  const submit = () => {
    const t = text.trim();
    // 任务运行中也允许提交：作为补充指令排队，下一回合注入对话
    if (!t || disabled) return;
    setPaletteOpen(false);
    onSend(t);
    setText("");
  };

  const primary = agents.filter((a) => a.mode !== "subagent");

  return (
    <div className="composer-wrap">
      {primary.length > 0 && (
        <div className="agent-bar" role="group" aria-label="选择 Agent">
          <span className="agent-bar-label">Agent:</span>
          {primary.map((a) => (
            <button
              key={a.id}
              className={`agent-btn ${currentAgent === a.id ? "active" : ""}`}
              title={a.description}
              onClick={() => onSelectAgent(a.id)}
              disabled={disabled || running}
            >
              {a.id}
            </button>
          ))}
          <span className="agent-bar-hint" title="按 Tab 在 Agent 之间切换">
            Tab
          </span>
          <span className="agent-bar-label">模型:</span>
          <select
            className="model-select"
            value={isDefaultModel ? "__global__" : effSelectValue}
            onChange={(e) => {
              const v = e.target.value;
              if (!v) return;
              if (v === globalSelectValue || v === "__global__") {
                // 选回全局默认 → 清除会话 override
                onSessionModelChange(null);
                return;
              }
              const [provider, model] = v.split("\n");
              onSessionModelChange({ provider, model });
            }}
            disabled={disabled || running}
            title={isDefaultModel ? `当前使用全局默认模型（${effProviderName} / ${effModel}），选择可切换为会话专用` : `当前会话模型：${effProviderName} / ${effModel}；选中“全局默认”可恢复`}
          >
            {/* 始终提供「全局默认」入口：显示实际生效的默认模型名，不再是抽象占位 */}
            <option value="__global__">
              {llmConfig?.active ? `${allProviders.find((p) => p.id === llmConfig.active)?.name || llmConfig.active} / ${llmConfig.providers[llmConfig.active]?.model || "未配置"}` : "未配置全局模型"}
              {isDefaultModel ? "" : "（默认）"}
            </option>
            {!isDefaultModel && sessionModel && !(sessionModel.model === globalModel && sessionModel.provider === llmConfig?.active) && (
              // 会话 override 的模型不在任何 provider 列表时，补一项避免 select 空白
              <option value={`${sessionModel.provider}\n${sessionModel.model}`}>
                {sessionModel.provider} / {sessionModel.model}（会话）
              </option>
            )}
            {allProviders.map((provider) =>
              provider.has_key && (provider.models ?? []).map((model) => (
                <option key={`${provider.id}:${model}`} value={`${provider.id}\n${model}`}>
                  {provider.name || provider.id} / {model}
                  {model === globalModel && provider.id === llmConfig?.active ? "（默认）" : ""}
                </option>
              ))
            )}
          </select>
          {onReasoningEffortChange && (
            <div className="reasoning-popover reasoning-quick">
              <button
                type="button"
                className={`reasoning-trigger reasoning-quick-trigger reasoning-effort-${displayEffort === "off" ? "off" : displayEffort}`}
                onClick={() => setReasoningOpen(!reasoningOpen)}
                title={
                  hasEffortOverride
                    ? `推理强度：${reasoningLabel(reasoningEffort)}（会话级）`
                    : providerEffort
                      ? `推理强度：${reasoningLabel(providerEffort)}（供应商默认，${effProviderName} 设置中配置）`
                      : "推理强度：关闭（未配置）"
                }
              >
                {reasoningLabel(displayEffort)}
              </button>
              {reasoningOpen && (
                <div className="reasoning-menu">
                  <div className="reasoning-track">
                    {providerEffort && (
                      <button
                        className={`reasoning-option ${!hasEffortOverride ? "active" : ""}`}
                        onClick={() => { onReasoningEffortChange(""); setReasoningOpen(false); }}
                        type="button"
                      >
                        <span className="reasoning-option-label">跟随全局 · {reasoningLabel(providerEffort)}</span>
                        <span className="reasoning-option-desc">使用供应商默认（{effProviderName} 设置中配置）</span>
                      </button>
                    )}
                    {[
                      { value: "off", label: "关闭", desc: "常规回答（本会话）" },
                      { value: "low", label: "低", desc: "轻量推理" },
                      { value: "medium", label: "中", desc: "平衡速度与深度" },
                      { value: "high", label: "高", desc: "深度推理" },
                      { value: "max", label: "最大", desc: "极限推理（Token 消耗大）" },
                    ].map((item) => (
                      <button
                        key={item.value}
                        className={`reasoning-option ${hasEffortOverride && reasoningEffort === item.value ? "active" : ""}`}
                        onClick={() => { onReasoningEffortChange(item.value); setReasoningOpen(false); }}
                        type="button"
                      >
                        <span className="reasoning-option-label">{item.label}</span>
                        <span className="reasoning-option-desc">{item.desc}</span>
                      </button>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      )}
      <div className="composer">
        {panelVisible && candidates.length > 0 && (
          <div className="command-palette" role="listbox">
            {candidates.slice(0, 8).map((c, i) => (
              <button
                key={c.name}
                className={`command-palette-item ${i === selIdx ? "active" : ""}`}
                onMouseDown={(e) => { e.preventDefault(); applySuggestion(c.name); }}
                onMouseEnter={() => setSelIdx(i)}
              >
                <span className="command-palette-name">/{c.name}</span>
                <span className="command-palette-desc">{c.description}</span>
                {c.hint && <span className="command-palette-hint">{c.hint}</span>}
              </button>
            ))}
          </div>
        )}
        <textarea
          autoFocus
          ref={inputRef}
          value={text}
          onChange={(e) => {
            const v = e.target.value;
            setText(v);
            // 仅首字符输入 "/" 时触发面板（消息中间的斜杠不触发）
            if (v.startsWith("/") && !paletteOpen) setPaletteOpen(true);
            if (!v.startsWith("/")) setPaletteOpen(false);
          }}
          onKeyDown={(e) => {
            if (panelVisible && candidates.length > 0) {
              if (e.key === "ArrowDown") {
                e.preventDefault();
                setSelIdx((i) => (i + 1) % candidates.length);
                return;
              }
              if (e.key === "ArrowUp") {
                e.preventDefault();
                setSelIdx((i) => (i - 1 + candidates.length) % candidates.length);
                return;
              }
              if (e.key === "Tab") {
                e.preventDefault();
                applySuggestion(candidates[Math.min(selIdx, candidates.length - 1)].name);
                return;
              }
              if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault();
                // 有候选且尚未带参数时，Enter 选中补全；已有参数则直接发送
                const needsArgs = pickingSkill ? false : (filtered[selIdx]?.argsHint ?? "") !== "";
                if (needsArgs && !restAfterCmd) {
                  applySuggestion(candidates[Math.min(selIdx, candidates.length - 1)].name);
                } else if (pickingSkill && filteredSkills[selIdx]) {
                  applySuggestion(filteredSkills[selIdx].name);
                } else {
                  submit();
                }
                return;
              }
              if (e.key === "Escape") {
                e.preventDefault();
                setPaletteOpen(false);
                return;
              }
            }
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              submit();
            }
          }}
          onBlur={() => setTimeout(() => setPaletteOpen(false), 150)}
          placeholder={running ? "任务进行中：输入将作为补充指令排队，■ 可停止" : `给 lite-code 下达任务…（输入 / 唤起命令面板）`}
          rows={3}
          disabled={disabled}
        />
        {running ? (
          <>
            <button className="btn-send" onClick={submit} disabled={disabled || !text.trim()} title="作为补充指令加入队列">
              ➤
            </button>
            <button className="btn-send btn-stop" onClick={onStop} title="停止任务">
              <span className="stop-icon" />
            </button>
          </>
        ) : (
          <button className="btn-send" onClick={submit} disabled={disabled || !text.trim()}>
            ➤
          </button>
        )}
      </div>
      <div className="composer-hint">
        {running ? "任务进行中：➤ 追加补充指令（排队注入），■ 停止任务" : "工具执行受安全策略保护，中危操作会请求你确认"}
      </div>
    </div>
  );
}
