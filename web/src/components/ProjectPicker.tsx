import { useCallback, useEffect, useState } from "react";
import { api } from "../api";

interface FsEntry {
  path: string;
  parent: string | null;
  home: string;
  is_workspace: boolean;
  dirs: string[];
  files: string[];
  truncated: boolean;
}

export default function ProjectPicker({
  initialPath,
  onClose,
  onSelect,
}: {
  initialPath: string;
  onClose: () => void;
  onSelect: (path: string) => void;
}) {
  const [current, setCurrent] = useState(initialPath);
  const [entry, setEntry] = useState<FsEntry | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async (path: string) => {
    setLoading(true);
    setError(null);
    try {
      const data = await api.fsList(path);
      setEntry(data);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load(current);
  }, [current, load]);

  const enter = (dir: string) => {
    setCurrent(entry ? `${entry.path}/${dir}` : `${current}/${dir}`);
  };

  const goHome = () => setCurrent(entry?.home ?? "~");
  const goUp = () => {
    if (entry?.parent) setCurrent(entry.parent);
  };

  const breadcrumb = (current || "").split("/").filter(Boolean);
  const jumpTo = (idx: number) => {
    const path = "/" + breadcrumb.slice(0, idx + 1).join("/");
    setCurrent(path);
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal project-picker" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h3>打开项目</h3>
          <button className="modal-close" onClick={onClose}>✕</button>
        </div>

        <div className="picker-pathbar">
          <button className="picker-nav" onClick={goHome} title="主目录">🏠</button>
          <button className="picker-nav" onClick={goUp} title="上级目录">⬆</button>
          <div className="picker-breadcrumb">
            {breadcrumb.map((seg, i) => (
              <span key={i}>
                <button className="crumb" onClick={() => jumpTo(i)}>{seg}</button>
                {i < breadcrumb.length - 1 && <span className="crumb-sep">/</span>}
              </span>
            ))}
          </div>
        </div>

        {error && <div className="picker-error">⚠ {error}</div>}

        <div className="picker-tree">
          {loading ? (
            <div className="picker-empty">加载中…</div>
          ) : entry ? (
            <>
              {entry.dirs.length === 0 && entry.files.length === 0 && (
                <div className="picker-empty">（空目录）</div>
              )}
              {entry.dirs.map((d) => (
                <button key={d} className="picker-row dir" onDoubleClick={() => enter(d)} onClick={() => enter(d)}>
                  <span className="picker-ico">📁</span>
                  <span className="picker-name">{d}</span>
                </button>
              ))}
              {entry.files.map((f) => (
                <div key={f} className="picker-row file" title={f}>
                  <span className="picker-ico">📄</span>
                  <span className="picker-name">{f}</span>
                </div>
              ))}
              {entry.truncated && <div className="picker-empty">…（条目过多，仅显示前 500 项）</div>}
            </>
          ) : null}
        </div>

        <div className="picker-footer">
          <span className="picker-path-label">{entry?.path ?? current}</span>
          <div className="picker-actions">
            <button className="btn-ghost" onClick={onClose}>取消</button>
            <button
              className="btn-primary"
              disabled={!entry}
              onClick={() => entry && onSelect(entry.path)}
            >
              选择此目录
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
