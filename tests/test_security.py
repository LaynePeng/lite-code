"""安全卫士测试（对应第15课：三级风险 + 动态黑白名单 + 路径拦截）。"""
from litecode.security.guard import SecurityGuard, ThreatLevel


def test_high_risk_rm_rf():
    r = SecurityGuard().check_shell_command("rm -rf /")
    assert r.level == ThreatLevel.HIGH


def test_high_risk_macos_system_commands():
    guard = SecurityGuard()
    assert guard.check_shell_command("csrutil disable").level == ThreatLevel.HIGH
    assert guard.check_shell_command("diskutil eraseDisk JHFS+ X disk2").level == ThreatLevel.HIGH
    assert guard.check_shell_command("rm -rf ~").level == ThreatLevel.HIGH
    assert guard.check_shell_command("rm -rf $HOME").level == ThreatLevel.HIGH


def test_medium_risk_macos_commands():
    guard = SecurityGuard()
    assert guard.check_shell_command("fdesetup remove").level == ThreatLevel.MEDIUM
    assert guard.check_shell_command("nvram -d boot-args").level == ThreatLevel.MEDIUM
    assert guard.check_shell_command("security delete-generic-password -s svc").level == ThreatLevel.MEDIUM
    assert guard.check_shell_command("osascript -e 'tell app \"Finder\" to quit'").level == ThreatLevel.MEDIUM
    assert guard.check_shell_command("softwareupdate --install -a").level == ThreatLevel.MEDIUM


def test_high_risk_fork_bomb_and_mkfs():
    guard = SecurityGuard()
    assert guard.check_shell_command("mkfs.ext4 /dev/sda1").level == ThreatLevel.HIGH
    assert guard.check_shell_command(":(){ :|:& };:").level == ThreatLevel.HIGH


def test_medium_risk_rm_and_sudo():
    guard = SecurityGuard()
    assert guard.check_shell_command("rm hello.txt").level == ThreatLevel.MEDIUM
    assert guard.check_shell_command("sudo apt install git").level == ThreatLevel.MEDIUM
    assert guard.check_shell_command("kill -9 1234").level == ThreatLevel.MEDIUM


def test_safe_command():
    guard = SecurityGuard()
    assert guard.check_shell_command("ls -la").level == ThreatLevel.SAFE
    assert guard.check_shell_command("git status").level == ThreatLevel.SAFE


def test_whitelist_prefix_bypasses_medium():
    # "git status" 白名单放行，即便其前缀不是中危也应为 SAFE
    guard = SecurityGuard()
    assert guard.check_shell_command("git status --short").level == ThreatLevel.SAFE


def test_forbidden_paths():
    guard = SecurityGuard()
    assert guard.check_path(".env").level == ThreatLevel.HIGH
    assert guard.check_path("src/.env.production").level == ThreatLevel.HIGH
    assert guard.check_path("~/.ssh/id_rsa").level == ThreatLevel.HIGH
    assert guard.check_path("src/main.ts").level == ThreatLevel.SAFE


def test_check_tool_for_file_tools():
    guard = SecurityGuard()
    r = guard.check_tool("read_file", {"filePath": "/etc/shadow"})
    assert r.level == ThreatLevel.HIGH
    r = guard.check_tool("read_file", {"filePath": "src/index.ts"})
    assert r.level == ThreatLevel.SAFE


def test_dynamic_config_reload():
    guard = SecurityGuard()
    assert guard.check_shell_command("custom-danger-xyz").level == ThreatLevel.SAFE

    guard.apply_config({
        "high_risk_patterns": [r"\bcustom-danger-xyz\b"],
        "medium_risk_patterns": [],
        "whitelist": [],
        "forbidden_paths": [],
    })
    assert guard.check_shell_command("custom-danger-xyz").level == ThreatLevel.HIGH
    # 热加载后原规则被替换
    assert guard.check_shell_command("rm -rf /").level == ThreatLevel.SAFE


def test_invalid_regex_ignored():
    guard = SecurityGuard()
    guard.apply_config({"high_risk_patterns": ["[invalid"]})
    assert guard.check_shell_command("anything").level == ThreatLevel.SAFE