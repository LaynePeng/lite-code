/** 应用图标（与 scripts/app-icon.svg 同源：渐变底 + 代码括号 + 光标） */
export default function AppIcon({ size = 30 }: { size?: number }) {
  return (
    <svg viewBox="0 0 1024 1024" width={size} height={size} xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
      <defs>
        <linearGradient id="app-icon-bg" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stop-color="#4F8CFF" />
          <stop offset="0.55" stop-color="#6C7BFF" />
          <stop offset="1" stop-color="#8B5CF6" />
        </linearGradient>
        <linearGradient id="app-icon-gloss" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stop-color="#ffffff" stopOpacity="0.30" />
          <stop offset="0.45" stop-color="#ffffff" stopOpacity="0.04" />
          <stop offset="1" stop-color="#ffffff" stopOpacity="0" />
        </linearGradient>
      </defs>
      <rect x="0" y="0" width="1024" height="1024" rx="190" fill="url(#app-icon-bg)" />
      <rect x="0" y="0" width="1024" height="560" rx="190" fill="url(#app-icon-gloss)" />
      <path d="M250 330h230a175 175 0 0 1 0 350H250" fill="none" stroke="#ffffff" strokeWidth="72" strokeLinecap="round" strokeLinejoin="round" />
      <rect x="628" y="256" width="120" height="512" rx="60" fill="#ffffff" />
      <rect x="646" y="396" width="84" height="232" rx="42" fill="rgba(139,92,246,0.35)" />
      <rect x="250" y="740" width="498" height="30" rx="15" fill="#ffffff" opacity="0.9" />
    </svg>
  );
}
