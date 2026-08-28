import { useState } from "react";

export default function Composer({
  disabled,
  onSend,
}: {
  disabled: boolean;
  onSend: (prompt: string) => void;
}) {
  const [text, setText] = useState("");

  const submit = () => {
    const t = text.trim();
    if (!t || disabled) return;
    onSend(t);
    setText("");
  };

  return (
    <div className="composer-wrap">
      <div className="composer">
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              submit();
            }
          }}
          placeholder="给 lite-code 下达任务…（Enter 发送，Shift+Enter 换行）"
          rows={1}
          disabled={disabled}
        />
        <button className="btn-send" onClick={submit} disabled={disabled || !text.trim()}>
          ➤
        </button>
      </div>
      <div className="composer-hint">工具执行受安全策略保护，中危操作会请求你确认</div>
    </div>
  );
}