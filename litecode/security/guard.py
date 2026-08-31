"""安全卫士（对应课程第15课 SecurityGuard，增强：动态黑白名单热加载）。

防御体系（Defense-in-Depth）：
- Layer 1: 敏感路径检查（/etc/shadow、.env、~/.ssh 等）
- Layer 2: 高危命令 AST/正则黑名单 → HIGH 直接阻断
- Layer 3: 中危模式 → MEDIUM 触发人工确认
- Layer 4: 动态白名单（精确前缀放行）覆盖默认检查
- 黑白名单可通过 .lite-code/config.json 动态热加载，无需改代码
"""
from __future__ import annotations

import json
import logging
import os
import re
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger("litecode.security")


class ThreatLevel(str, Enum):
    SAFE = "SAFE"
    MEDIUM = "MEDIUM"  # 需要用户手动确认
    HIGH = "HIGH"  # 直接拒绝


class SecurityCheckResult:
    def __init__(self, level: ThreatLevel, reason: str = "") -> None:
        self.level = level
        self.reason = reason

    def to_dict(self) -> Dict[str, Any]:
        return {"level": self.level.value, "reason": self.reason}


DEFAULT_FORBIDDEN_PATHS = [
    "/etc/shadow",
    "/etc/passwd",
    "/etc/sudoers",
    "~/.ssh",
    "~/.bashrc",
    "~/.zshrc",
    "~/.profile",
    "~/.aws",
    "~/.config/gh",
    ".env",
    "id_rsa",
    "id_ed25519",
]

DEFAULT_HIGH_RISK_PATTERNS = [
    r"\brm\s+-[rRfF]+\s+[/\*]",  # rm -rf / 或 rm -rf *
    r">\s*/dev/sd[a-z]",  # 覆盖块设备
    r"\bmkfs\b",  # 格式化磁盘
    r"\bdd\b.*\bof=/dev/",  # 写入物理设备
    r":\(\)\{\s*:\|\:&\s*\};:",  # Fork 炸弹
    r"\bchmod\s+-R\s+777\s+/",  # 破坏根目录权限
    r"\bshutdown\b",  # 关机
    r"\breboot\b",  # 重启
    r"curl[^\n]*\|\s*(ba)?sh\b",  # 远程脚本直接执行
    r"wget[^\n]*\|\s*(ba)?sh\b",
    r"git\s+push\s+.*--force",  # 强制推送
    r"git\s+reset\s+--hard",  # 硬重置（可能丢失改动）
    r"git\s+clean\s+-f[dx]?",  # 清理未跟踪文件
    r"\brm\s+-rf\s+(~/|\./)?\*\b",
    r"\bkillall\s+.*(?:node|python|npm)",  # 批量杀进程
    r"\bdel\b[^\n]*\s[A-Za-z]:\\\*",  # Windows: del /s /q C:\*（盘符根通配删除）
    r"\b(?:rmdir|rd)\b[^\n]*\s[A-Za-z]:\\",  # Windows: rmdir /rd /s /q C:\（删除盘符根目录）
    r"\bRemove-Item\b[^\n]*(?:-Recurse|-Force)[^\n]*\s[A-Za-z]:\\",  # PowerShell: Remove-Item -Recurse -Force C:\
    r"\bcsrutil\b",  # macOS: 关闭 SIP（系统完整性保护）
    r"\bdiskutil\b",  # macOS: 磁盘操作（eraseDisk/zeroDisk/secureErase 等）
    r"\brm\s+-rf\s+(?:~|\$HOME)(?:\s|$)",  # macOS/Linux: 删除整个家目录（rm -rf ~ / $HOME）
]

DEFAULT_MEDIUM_RISK_PATTERNS = [
    r"\brm\b",  # 普通删除操作（Unix / PowerShell）
    r"\bdel\b",  # Windows: 删除文件（cmd / PowerShell 别名）
    r"\berase\b",  # Windows: 删除文件（cmd / PowerShell 别名）
    r"\bRemove-Item\b",  # Windows: PowerShell 删除（含 -Recurse/-Force）
    r"\brmdir\b",  # Windows: 删除目录
    r"\brd\b",  # Windows: rmdir 别名（删除目录）
    r"\bos\.(?:remove|unlink)\b|\.unlink\(",  # Python 脚本删除文件
    r"\bfdesetup\b",  # macOS: FileVault 加密管理（可移除磁盘加密）
    r"\bnvram\s+(?:-d|-c)\b",  # macOS: 删除固件变量
    r"\bsecurity\s+delete-(?:keychain|generic-password|internet-password)\b",  # macOS: 删除钥匙串凭据
    r"\bosascript\b",  # macOS: AppleScript 执行（可控制系统与应用）
    r"\bsoftwareupdate\b",  # macOS: 系统更新
    r"\bkill\s+-9\b",  # 强制终止进程
    r"\bcurl\b.*\|\s*(ba)?sh\b",  # 管道远程脚本
    r"\bsudo\b",  # 提权操作
    r"\bgit\s+push\b",  # 推送
    r"\bgit\s+rebase\b",
    r"\bgit\s+checkout\s+-[bBfF]\b",
    r"\bgit\s+branch\s+-[dD]\b",
    r"\bmv\s+\S+\s+/",  # 移动到根目录
    r"\btruncate\b",  # 截断文件
    r"\b>+\s*\S+\.(?:log|db|sqlite)\b",  # 覆盖日志/数据库
    r"\bdrop\s+database\b",
    r"\bpython[^\n]*-m\s+venv[^\n]*--clear\b",
    r"\bpnpm?\s+(?:install|i)\b[^\n]*--force",
    r"\bchmod\s+777\b",
]

DEFAULT_WHITELIST = [
    "git status",
    "git log",
    "git diff",
    "git branch",
    "ls",
    "pwd",
    "whoami",
    "echo",
    "cat ",
    "head ",
    "tail ",
    "npm run test",
    "npm test",
    "pytest",
    "python3 -m pytest",
    "rg ",
    "tree ",
    "cd ",
    "mkdir ",
    "touch ",
    "cp ",
    "git add",
    "git commit",
    "git checkout ",
    "git fetch",
    "git pull",
    "git push",
]

SENSITIVE_ENV_VARS = [
    "DEEPSEEK_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_ACCESS_KEY_ID",
    "GITHUB_TOKEN",
    "GH_TOKEN",
    "GITLAB_TOKEN",
    "DATABASE_URL",
    "MYSQL_PWD",
    "PGPASSWORD",
    "REDIS_PASSWORD",
    "HUGGING_FACE_HUB_TOKEN",
    "HF_TOKEN",
]


class SecurityGuard:
    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        self.forbidden_paths: List[str] = list(DEFAULT_FORBIDDEN_PATHS)
        self.high_risk_patterns: List[str] = list(DEFAULT_HIGH_RISK_PATTERNS)
        self.medium_risk_patterns: List[str] = list(DEFAULT_MEDIUM_RISK_PATTERNS)
        self.whitelist: List[str] = list(DEFAULT_WHITELIST)
        self._compiled_high = self._compile(self.high_risk_patterns)
        self._compiled_medium = self._compile(self.medium_risk_patterns)
        if config:
            self.apply_config(config)

    # ------------------------------------------------------------ 配置热加载

    def apply_config(self, config: Dict[str, Any]) -> None:
        """动态更新黑白名单（.lite-code/config.json），无需重启。"""
        if "forbidden_paths" in config:
            self.forbidden_paths = list(config["forbidden_paths"])
        if "high_risk_patterns" in config:
            self.high_risk_patterns = list(config["high_risk_patterns"])
        if "medium_risk_patterns" in config:
            self.medium_risk_patterns = list(config["medium_risk_patterns"])
        if "whitelist" in config:
            self.whitelist = list(config["whitelist"])
        self._compiled_high = self._compile(self.high_risk_patterns)
        self._compiled_medium = self._compile(self.medium_risk_patterns)
        logger.info("[SecurityGuard] 动态规则已热加载 (%d 高危 / %d 中危 / %d 白名单)",
                    len(self._compiled_high), len(self._compiled_medium), len(self.whitelist))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "forbidden_paths": self.forbidden_paths,
            "high_risk_patterns": self.high_risk_patterns,
            "medium_risk_patterns": self.medium_risk_patterns,
            "whitelist": self.whitelist,
        }

    @staticmethod
    def _compile(patterns: List[str]) -> List[re.Pattern]:
        compiled = []
        for p in patterns:
            try:
                compiled.append(re.compile(p, re.IGNORECASE))
            except re.error:
                logger.warning("[SecurityGuard] 非法正则被忽略: %s", p)
        return compiled

    # ------------------------------------------------------------ 路径检查

    def check_path(self, file_path: str) -> SecurityCheckResult:
        normalized = file_path.replace("\\", "/").lower()
        normalized = os.path.expanduser(normalized).lower()
        for forbidden in self.forbidden_paths:
            key = forbidden.replace("\\", "/").lower().lstrip("./")
            if key and key in normalized:
                return SecurityCheckResult(
                    ThreatLevel.HIGH,
                    f'访问敏感路径 "{forbidden}" 被严格禁止！',
                )
        return SecurityCheckResult(ThreatLevel.SAFE)

    def is_external_path(self, workspace: str, file_path: str) -> bool:
        """判断路径是否位于当前项目之外（不改变敏感路径检查结果）。"""
        raw = os.path.expanduser(str(file_path))
        target = os.path.abspath(raw if os.path.isabs(raw) else os.path.join(workspace, raw))
        root = os.path.abspath(workspace)
        return target != root and not target.startswith(root + os.sep)

    # ------------------------------------------------------------ 命令检查

    def check_shell_command(self, command: str) -> SecurityCheckResult:
        # 0. 白名单精确前缀放行（动态白名单增强）
        stripped = command.strip()
        for prefix in self.whitelist:
            if stripped.startswith(prefix):
                return SecurityCheckResult(ThreatLevel.SAFE)

        # 1. 高危黑名单拦截
        for pattern in self._compiled_high:
            if pattern.search(command):
                return SecurityCheckResult(
                    ThreatLevel.HIGH,
                    f'命令命中高危黑名单规则: /{pattern.pattern}/',
                )

        # 2. 中危模式（提权 / 破坏性操作）→ 人工确认
        for pattern in self._compiled_medium:
            if pattern.search(command):
                return SecurityCheckResult(
                    ThreatLevel.MEDIUM,
                    f'命令需要人工确认: {command[:200]}',
                )

        return SecurityCheckResult(ThreatLevel.SAFE)

    # ------------------------------------------------------------ 工具检查

    def check_tool(self, tool_name: str, args: Dict[str, Any]) -> SecurityCheckResult:
        if tool_name in ("read_file", "write_file", "list_dir", "apply_search_replace",
                         "apply_unified_diff", "get_file_outline", "read_focused_symbol"):
            path = args.get("filePath") or args.get("path") or ""
            if path:
                result = self.check_path(path)
                if result.level != ThreatLevel.SAFE:
                    return result
        if tool_name in ("webfetch", "webfetch_batch"):
            urls = [args.get("url")] if tool_name == "webfetch" else (args.get("urls") or [])
            if not isinstance(urls, list):
                urls = [urls]
            for u in urls:
                if u and not str(u).lower().startswith(("http://", "https://")):
                    return SecurityCheckResult(
                        ThreatLevel.HIGH,
                        f'webfetch 仅允许 http/https 协议: {str(u)[:100]}',
                    )
        return SecurityCheckResult(ThreatLevel.SAFE)
