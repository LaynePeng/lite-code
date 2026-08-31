import { useState } from "react";
import type { AgentInfo, LLMConfig, SessionModel } from "../types";

export default function Composer({
  disabled,
  running,
  agents,
  currentAgent,
  onSelectAgent,
  onSend,
  onStop,
  llmConfig,
  sessionModel,
  onSessionModelChange,
}: {
  disabled: boolean;
  running: boolean;
  agents: AgentInfo[];
  currentAgent: string;
  onSelectAgent: (id: string) => void;
  onSend: (prompt: string) => void;
  onStop: () => void;
  llmConfig: LLMConfig | null;
  sessionModel: SessionModel | null;
  onSessionModelChange: (model: SessionModel | null) => void;
}) {
  const [text, setText] = useState("");

  const submit = () => {
    const t = text.trim();
    if (!t || disabled || running) return;
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
            value={sessionModel ? `${sessionModel.provider}\n${sessionModel.model}` : "__default__"}
            onChange={(e) => {
              if (e.target.value === "__default__") {
                onSessionModelChange(null);
                return;
              }
              const [provider, model] = e.target.value.split("\n");
              onSessionModelChange({ provider, model });
            }}
            disabled={disabled || running}
            title="只影响当前会话，正在运行的任务不会切换"
          >
            <option value="__default__">系统默认</option>
            {llmConfig && Object.entries(llmConfig.providers).map(([pid, provider]) =>
              provider.has_key && (provider.models ?? []).map((model) => (
                <option key={`${pid}:${model}`} value={`${pid}\n${model}`}>
                  {provider.name || pid} / {model}
                </option>
              ))
            )}
          </select>
        </div>
      )}
      <div className="composer">
        <textarea
          autoFocus
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              submit();
            }
          }}
          placeholder={running ? "任务进行中…" : `给 lite-code 下达任务…（当前 Agent: ${currentAgent}，Tab 切换）`}
          rows={1}
          disabled={disabled || running}
        />
        {running ? (
          <button className="btn-send btn-stop" onClick={onStop} title="停止任务">
            <span className="stop-icon" />
          </button>
        ) : (
          <button className="btn-send" onClick={submit} disabled={disabled || !text.trim()}>
            ➤
          </button>
        )}
      </div>
      <div className="composer-hint">
        {running ? "任务进行中，点击 ■ 停止" : "工具执行受安全策略保护，中危操作会请求你确认"}
      </div>
    </div>
  );
}
