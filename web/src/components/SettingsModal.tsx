import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import type { LLMConfig, LLMProviderMeta, LLMProviderSettings, MCPServerConfig, MCPServerStatus } from "../types";

export default function SettingsModal({
  onClose,
  onSaved,
}: {
  onClose: () => void;
  onSaved: () => void;
}) {
  const [providers, setProviders] = useState<LLMProviderMeta[]>([]);
  const [config, setConfig] = useState<LLMConfig | null>(null);
  const [activeProvider, setActiveProvider] = useState("");
  const [editing, setEditing] = useState<Record<string, Partial<LLMProviderSettings>>>({});
  const [testResult, setTestResult] = useState<{ ok: boolean; message: string } | null>(null);
  const [testing, setTesting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [activeTab, setActiveTab] = useState<"llm" | "mcp">("llm");

  // MCP 服务器配置（独立于 LLM 配置保存，保存即热重连）
  const [mcpServers, setMcpServers] = useState<Record<string, MCPServerConfig>>({});
  const [mcpArgsText, setMcpArgsText] = useState<Record<string, string>>({});
  const [mcpStatus, setMcpStatus] = useState<MCPServerStatus[]>([]);
  const [mcpSaving, setMcpSaving] = useState(false);
  const [mcpResult, setMcpResult] = useState<string | null>(null);

  // 自定义 Header 的编辑文本（每行 "Key: Value" 或 "Key=Value"），blur 时解析提交
  const [headersText, setHeadersText] = useState<Record<string, string>>({});

  useEffect(() => {
    Promise.all([api.llmProviders(), api.llmConfig(), api.mcpStatus()]).then(([p, c, m]) => {
      setProviders(p);
      setConfig(c);
      setActiveProvider(c.active);
      // 初始化编辑状态
      const edit: Record<string, Partial<LLMProviderSettings>> = {};
      for (const [pid, s] of Object.entries(c.providers)) {
        edit[pid] = { ...s };
      }
      setEditing(edit);
      // MCP：用运行状态初始化可编辑配置
      setMcpStatus(m.servers || []);
      const cfg: Record<string, MCPServerConfig> = {};
      for (const s of m.servers || []) {
        cfg[s.name] = { command: s.command, args: s.args, enabled: s.enabled };
      }
      setMcpServers(cfg);
      // 自定义 Header：dict → 每行 "Key: Value" 的可编辑文本
      const texts: Record<string, string> = {};
      for (const [pid, s] of Object.entries(c.providers)) {
        const headers = (s as LLMProviderSettings).custom_headers || {};
        texts[pid] = Object.entries(headers).map(([k, v]) => `${k}: ${v}`).join("\n");
      }
      setHeadersText(texts);
    }).catch(() => {});
  }, []);

  // ------------------------------------------------------------ MCP 编辑

  const updateMcp = (name: string, patch: Partial<MCPServerConfig>) => {
    setMcpServers((prev) => ({ ...prev, [name]: { ...(prev[name] || { command: "" }), ...patch } }));
  };

  const addMcpServer = () => {
    const name = `server_${Date.now().toString().slice(-6)}`;
    setMcpServers((prev) => ({ ...prev, [name]: { command: "", args: [], enabled: true } }));
  };

  const removeMcpServer = (name: string) => {
    setMcpServers((prev) => {
      const copy = { ...prev };
      delete copy[name];
      return copy;
    });
  };

  const saveMcp = useCallback(async () => {
    setMcpSaving(true);
    setMcpResult(null);
    try {
      const payload: Record<string, MCPServerConfig> = {};
      for (const [name, s] of Object.entries(mcpServers)) {
        const command = (s.command || "").trim();
        if (!command) continue; // 空 command 的条目不保存
        payload[name] = {
          command,
          args: Array.isArray(s.args) ? s.args : String(s.args || "").split(/\s+/).filter(Boolean),
          enabled: s.enabled !== false,
        };
      }
      const res = await api.updateMcpServers(payload);
      setMcpStatus(res.servers || []);
      setMcpResult("已保存并重连（新任务生效，MCP 工具调用需审批）");
      onSaved();
    } catch (err) {
      setMcpResult(`保存失败: ${(err as Error).message}`);
    } finally {
      setMcpSaving(false);
    }
  }, [mcpServers, onSaved]);

  const update = (pid: string, key: string, value: string | string[] | number | boolean | null | Record<string, string>) => {
    setEditing((e) => ({ ...e, [pid]: { ...(e[pid] || {}), [key]: value } }));
  };

  // 自定义 Header 文本 → dict：每行按第一个冒号/等号切分（值可含冒号，如 URL），
  // 空行、# 注释、无分隔符的行忽略；同键后者覆盖。
  const parseHeaders = (text: string): Record<string, string> => {
    const headers: Record<string, string> = {};
    for (const line of text.split("\n")) {
      const trimmed = line.trim();
      if (!trimmed || trimmed.startsWith("#")) continue;
      const sepIdx = Math.min(
        ...[trimmed.indexOf(":"), trimmed.indexOf("=")].filter((i) => i > 0).concat([Infinity]),
      );
      if (!Number.isFinite(sepIdx)) continue;
      const key = trimmed.slice(0, sepIdx).trim();
      const value = trimmed.slice(sepIdx + 1).trim();
      if (key && value) headers[key] = value;
    }
    return headers;
  };

  const commitHeaders = (pid: string, text: string) => {
    update(pid, "custom_headers", parseHeaders(text));
  };

  const providerMeta = providers.find((p) => p.id === activeProvider);
  const currentEdit = editing[activeProvider] || {};
  const availableModels = (currentEdit.models as string[] | undefined) ?? providerMeta?.models ?? [];
  const isCustom = activeProvider.startsWith("custom_");

  const addCustomProvider = () => {
    const id = `custom_${Date.now()}`;
    const next: LLMProviderSettings = {
      api_key: "", has_key: false, base_url: "", model: "", models: [],
      temperature: 0.2, name: "自定义供应商", custom_headers: {},
    };
    setProviders((prev) => [...prev, {
      id, name: next.name || id, kind: "openai", models: [], default_base_url: "",
      has_key: false, model: "",
    }]);
    setEditing((prev) => ({ ...prev, [id]: next }));
    setActiveProvider(id);
  };

  const removeCustomProvider = () => {
    if (!isCustom) return;
    const next = providers.filter((p) => p.id !== activeProvider);
    setProviders(next);
    setEditing((prev) => { const copy = { ...prev }; delete copy[activeProvider]; return copy; });
    setActiveProvider(next[0]?.id || "deepseek");
  };

  const handleTest = useCallback(async () => {
    if (!activeProvider) return;
    setTesting(true);
    setTestResult(null);
    try {
      const overrides: Record<string, unknown> = {};
      const e = editing[activeProvider];
      if (e?.api_key && !e.api_key.includes("…") && e.api_key !== "****") overrides.api_key = e.api_key;
      if (e?.base_url) overrides.base_url = e.base_url;
      if (e?.model) overrides.model = e.model;
      if (e?.temperature) overrides.temperature = e.temperature;
      // 测试连接带上当前编辑的自定义 Header（含未 blur 的文本框内容）
      const liveText = headersText[activeProvider];
      if (liveText !== undefined) {
        overrides.custom_headers = parseHeaders(liveText);
      } else if (e?.custom_headers !== undefined) {
        overrides.custom_headers = e.custom_headers;
      }
      const res = await api.testLLM(activeProvider, Object.keys(overrides).length ? overrides : undefined);
      setTestResult(res);
    } catch (err) {
      setTestResult({ ok: false, message: (err as Error).message });
    } finally {
      setTesting(false);
    }
  }, [activeProvider, editing, headersText]);

  const handleSave = useCallback(async () => {
    setSaving(true);
    try {
      const providersPayload: Record<string, Partial<LLMProviderSettings>> = {};
      for (const pid of Object.keys(editing)) {
        providersPayload[pid] = { ...editing[pid] };
        if (headersText[pid] !== undefined) {
          providersPayload[pid].custom_headers = parseHeaders(headersText[pid]);
        }
      }
      const newConfig = await api.updateLLMConfig(activeProvider, providersPayload);
      setConfig(newConfig);
      setTestResult({ ok: true, message: "配置已保存" });
      onSaved();
    } catch (err) {
      setTestResult({ ok: false, message: (err as Error).message });
    } finally {
      setSaving(false);
    }
  }, [activeProvider, editing, onSaved]);

  return (
      <div className="modal-overlay">
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2>⚙️ 设置</h2>
          <button className="modal-close" onClick={onClose}>✕</button>
        </div>

        <div className="modal-body">
          <div className="settings-tabs">
            <button
              className={`settings-tab ${activeTab === "llm" ? "active" : ""}`}
              onClick={() => setActiveTab("llm")}
            >
              LLM
            </button>
            <button
              className={`settings-tab ${activeTab === "mcp" ? "active" : ""}`}
              onClick={() => setActiveTab("mcp")}
            >
              MCP Server
            </button>
          </div>

          {activeTab === "llm" ? (
            <>
              <div className="settings-section">
                <h3>LLM 供应商</h3>
                <div className="provider-selector">
                  {providers.map((p) => (
                    <button
                      key={p.id}
                      className={`provider-btn ${p.id === activeProvider ? "active" : ""}`}
                      onClick={() => setActiveProvider(p.id)}
                    >
                      <span className="provider-name">{p.name}</span>
                      <span className={`provider-status ${p.has_key ? "configured" : "unconfigured"}`}>
                        {p.has_key ? "已配置" : "未配置"}
                      </span>
                    </button>
                  ))}
                  <button className="provider-btn" onClick={addCustomProvider}>＋ 自定义供应商</button>
                </div>
              </div>

              {providerMeta && (
                <div className="settings-section" key={activeProvider}>
                  <h3>{providerMeta.name} 配置</h3>

                  {isCustom && (
                    <div className="form-group">
                      <label>供应商名称</label>
                      <input className="form-input" value={(currentEdit.name as string) || providerMeta.name}
                        onChange={(e) => update(activeProvider, "name", e.target.value)} />
                    </div>
                  )}

                  <div className="form-group">
                    <label>API Key</label>
                    <input
                      type="password"
                      className="form-input"
                      placeholder={currentEdit.has_key ? "已配置，输入新值覆盖" : "输入 API Key"}
                      value={(currentEdit.api_key as string) || ""}
                      onChange={(e) => update(activeProvider, "api_key", e.target.value)}
                    />
                  </div>

                  <div className="form-group">
                    <label>接口地址 (Base URL)</label>
                    <input
                      type="text"
                      className="form-input"
                      value={(currentEdit.base_url as string) || ""}
                      onChange={(e) => update(activeProvider, "base_url", e.target.value)}
                    />
                  </div>

                  <div className="form-group">
                    <label>自定义 Header（每行一个，可留空）</label>
                    <textarea
                      className="form-input"
                      rows={3}
                      placeholder={"X-Title: My App\nHTTP-Referer: https://myapp.dev"}
                      value={headersText[activeProvider] ?? ""}
                      onChange={(e) =>
                        setHeadersText((prev) => ({ ...prev, [activeProvider]: e.target.value }))
                      }
                      onBlur={(e) => commitHeaders(activeProvider, e.target.value)}
                    />
                    <small>
                      格式 <code>Key: Value</code> 或 <code>Key=Value</code>（按第一个分隔符切分，值可含冒号）；
                      支持任意多个，可覆盖默认 Authorization 头；<code>#</code> 开头的行忽略。
                    </small>
                  </div>

                  <div className="form-group">
                    <label>模型</label>
                    <div className="model-input-group">
                      <input
                        type="text"
                        className="form-input"
                        list={`models-${activeProvider}`}
                        placeholder="输入模型名或从列表选择"
                        value={(currentEdit.model as string) || ""}
                        onChange={(e) => update(activeProvider, "model", e.target.value)}
                      />
                      <datalist id={`models-${activeProvider}`}>
                        {availableModels.map((m) => (
                          <option key={m} value={m} />
                        ))}
                      </datalist>
                    </div>
                  </div>

                  <div className="form-group">
                    <label>该供应商的模型列表（每行一个，可添加多个）</label>
                    <textarea
                      className="form-input"
                      rows={Math.min(6, Math.max(2, availableModels.length))}
                      value={availableModels.join("\n")}
                      onChange={(e) => update(
                        activeProvider,
                        "models",
                        e.target.value.split("\n").map((m) => m.trim()).filter(Boolean),
                      )}
                    />
                    <small>当前上方“模型”字段是实际调用的模型，模型列表用于快速切换和保留多个模型。</small>
                  </div>

                  <div className="form-group">
                    <label>温度 (Temperature): {currentEdit.temperature ?? 0.2}</label>
                    <input
                      type="range"
                      min="0"
                      max="2"
                      step="0.1"
                      className="form-range"
                      value={currentEdit.temperature ?? 0.2}
                      onChange={(e) => update(activeProvider, "temperature", parseFloat(e.target.value))}
                    />
                  </div>

                  <div className="form-group">
                    <label>上下文长度 tokens（留空自动：models.dev 同步/内置表）</label>
                    <input
                      type="number"
                      min="1000"
                      step="1000"
                      className="form-input"
                      placeholder="如 1000000（DeepSeek V4）"
                      value={(currentEdit.context_window as number) ?? ""}
                      onChange={(e) => {
                        const v = e.target.value;
                        update(activeProvider, "context_window", v === "" ? null : parseInt(v, 10));
                      }}
                    />
                  </div>

                  <div className="form-actions">
                    <button className="btn-test" onClick={handleTest} disabled={testing}>
                      {testing ? "测试中…" : "🔄 测试连接"}
                    </button>
                    <button className="btn-save-settings" onClick={handleSave} disabled={saving}>
                      {saving ? "保存中…" : "💾 保存配置"}
                    </button>
                    {isCustom && <button className="btn-test" onClick={removeCustomProvider}>删除供应商</button>}
                  </div>

                  {testResult && (
                    <div className={`test-result ${testResult.ok ? "ok" : "error"}`}>
                      {testResult.ok ? "✅ " : "❌ "}{testResult.message}
                    </div>
                  )}
                </div>
              )}
            </>
          ) : (
            <div className="settings-section">
              <div className="mcp-section-head">
                <h3>MCP Server（stdio）</h3>
                <button className="btn-test" onClick={addMcpServer}>＋ 添加</button>
              </div>
              <p className="mcp-hint">
                外部工具服务器通过 stdio 连接，保存后自动重连；工具以
                <code>mcp_&lt;server&gt;_&lt;tool&gt;</code> 注册，调用时会请求审批。
              </p>
              {Object.entries(mcpServers).map(([name, s]) => {
                const st = mcpStatus.find((x) => x.name === name);
                return (
                  <div className="mcp-server-card" key={name}>
                    <div className="mcp-server-row">
                      <input
                        className="form-input mcp-name"
                        value={name}
                        readOnly
                        title="服务器名（新增后不可改，删除重建）"
                      />
                      <label className="mcp-toggle" title="禁用的服务器不连接">
                        <input
                          type="checkbox"
                          checked={s.enabled !== false}
                          onChange={(e) => updateMcp(name, { enabled: e.target.checked })}
                        />
                        启用
                      </label>
                      {st && st.connected && <span className="mcp-badge ok" title={st.tools.join(", ")}>
                        已连接 · {st.tools.length} 工具
                      </span>}
                      {st && !st.connected && st.error && <span className="mcp-badge err" title={st.error}>连接失败</span>}
                      <button className="btn-test mcp-remove" onClick={() => removeMcpServer(name)}>删除</button>
                    </div>
                    <div className="mcp-server-row">
                      <input
                        className="form-input"
                        placeholder="启动命令，如 npx / uvx / python"
                        value={s.command || ""}
                        onChange={(e) => updateMcp(name, { command: e.target.value })}
                      />
                    </div>
                    <div className="mcp-server-row">
                      <input
                        className="form-input"
                        placeholder="参数（空格分隔），如 -y @modelcontextprotocol/server-sqlite ./data.db"
                        value={mcpArgsText[name] ?? (Array.isArray(s.args) ? s.args.join(" ") : "")}
                        onChange={(e) => setMcpArgsText((prev) => ({ ...prev, [name]: e.target.value }))}
                        onBlur={(e) => updateMcp(name, {
                          args: e.target.value.split(/\s+/).filter(Boolean),
                        })}
                      />
                    </div>
                    {st && !st.connected && st.error && <div className="mcp-error-detail">{st.error}</div>}
                  </div>
                );
              })}
              {Object.keys(mcpServers).length === 0 && (
                <div className="mcp-empty">暂未配置 MCP Server</div>
              )}
              <div className="form-actions">
                <button className="btn-save-settings" onClick={saveMcp} disabled={mcpSaving}>
                  {mcpSaving ? "重连中…" : "💾 保存 MCP 配置"}
                </button>
              </div>
              {mcpResult && <div className="test-result ok">{mcpResult}</div>}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
