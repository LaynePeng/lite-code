import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import type { LLMConfig, LLMProviderMeta, LLMProviderSettings } from "../types";

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

  useEffect(() => {
    Promise.all([api.llmProviders(), api.llmConfig()]).then(([p, c]) => {
      setProviders(p);
      setConfig(c);
      setActiveProvider(c.active);
      // 初始化编辑状态
      const edit: Record<string, Partial<LLMProviderSettings>> = {};
      for (const [pid, s] of Object.entries(c.providers)) {
        edit[pid] = { ...s };
      }
      setEditing(edit);
    }).catch(() => {});
  }, []);

  const update = (pid: string, key: string, value: string | number | boolean) => {
    setEditing((e) => ({ ...e, [pid]: { ...(e[pid] || {}), [key]: value } }));
  };

  const providerMeta = providers.find((p) => p.id === activeProvider);
  const currentEdit = editing[activeProvider] || {};

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
      const res = await api.testLLM(activeProvider, Object.keys(overrides).length ? overrides : undefined);
      setTestResult(res);
    } catch (err) {
      setTestResult({ ok: false, message: (err as Error).message });
    } finally {
      setTesting(false);
    }
  }, [activeProvider, editing]);

  const handleSave = useCallback(async () => {
    setSaving(true);
    try {
      const providersPayload: Record<string, Partial<LLMProviderSettings>> = {};
      for (const pid of Object.keys(editing)) {
        providersPayload[pid] = { ...editing[pid] };
      }
      const newConfig = await api.updateLLMConfig(activeProvider, providersPayload);
      setConfig(newConfig);
      onSaved();
    } catch (err) {
      setTestResult({ ok: false, message: (err as Error).message });
    } finally {
      setSaving(false);
    }
  }, [activeProvider, editing, onSaved]);

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2>⚙️ 设置</h2>
          <button className="modal-close" onClick={onClose}>✕</button>
        </div>

        <div className="modal-body">
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
            </div>
          </div>

          {providerMeta && (
            <div className="settings-section" key={activeProvider}>
              <h3>{providerMeta.name} 配置</h3>

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
                    {providerMeta.models.map((m) => (
                      <option key={m} value={m} />
                    ))}
                  </datalist>
                </div>
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

              <div className="form-actions">
                <button className="btn-test" onClick={handleTest} disabled={testing}>
                  {testing ? "测试中…" : "🔄 测试连接"}
                </button>
                <button className="btn-save-settings" onClick={handleSave} disabled={saving}>
                  {saving ? "保存中…" : "💾 保存配置"}
                </button>
              </div>

              {testResult && (
                <div className={`test-result ${testResult.ok ? "ok" : "error"}`}>
                  {testResult.ok ? "✅ " : "❌ "}{testResult.message}
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}