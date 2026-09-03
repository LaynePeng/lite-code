# -*- coding: utf-8 -*-
"""单次扫描替换教程正文中的课程编号引用（旧编号→新编号），避免二次映射。"""
import re, os, glob, sys

BOOK = "AI-Coding-Agent-Dummy-Book"

# 旧→新 编号映射（纯数字引用）
NUM_MAP = {7:6, 8:7, 9:7, 10:8, 11:9, 12:10, 13:10, 14:11, 15:12,
           16:14, 17:15, 18:16, 19:18, 20:21, 21:23, 22:24}

# 标题引用映射：旧标题全文 → 新标题全文
TITLE_MAP = {
    # 第10课 → 第8课
    "第10课：项目指令文件与Skills系统": "第8课：Skills 系统：注入机制与权限治理",
    # 第11课 → 第9课
    "第11课：标准Model Context Protocol（MCP）接入": "第9课：标准 Model Context Protocol（MCP）接入",
    "第11课：标准 Model Context Protocol (MCP) 接入": "第9课：标准 Model Context Protocol（MCP）接入",
    # 第12课 → 第10课
    "第12课：Cordis 插件内核设计（Spatiotemporal Composability）": "第10课：插件架构：内核设计与依赖注入",
    "第12课：Cordis插件内核设计（Spatiotemporal Composability）": "第10课：插件架构：内核设计与依赖注入",
    # 第14课 → 第11课
    "第14课：多 Agent 协作与子 Agent 调度 (Sub-agent Orchestration)": "第11课：多 Agent 协作：编排、通信与持久化",
    # 第15课 → 第12课
    "第15课：Agent 类型与自定义机制（Build/Plan 与用户扩展）": "第12课：Agent 类型与自定义机制",
    # 第17课 → 第15课
    "第17课：LLM 多供应商适配器与核心工具集 (`lite-code` 实战第二篇)": "第15课：LLM 多供应商适配器与核心工具集（lite-code 实战第二篇）",
    # 第18课 → 第16课
    "第18课：AgentLoop 主循环 (`lite-code` 实战第三篇)": "第16课：AgentLoop 主循环与增强集成（lite-code 实战第三篇）",
    # 第19课 → 第18课
    "第19课：安全沙箱与高危拦截实战 (`lite-code` 实战第四篇)": "第18课：安全沙箱与高危拦截实战（lite-code 实战第四篇）",
    # 第20课 → 第21课
    "第20课：Web UI 实战 (`lite-code` 实战第五篇)": "第21课：Web UI 实战（lite-code 实战第五篇）",
    # 第21课 → 第23课
    "第21课：Electron 桌面应用": "第23课：Electron 桌面应用（lite-code 实战第六篇）",
    # 第22课 → 第24课
    "第22课：工程实践与踩坑（lite-code实战终章）": "第24课：工程实践与踩坑（lite-code 实战终章）",
}

# 构建单次扫描正则：标题模式（最长优先）| 纯数字模式
title_patterns = sorted([re.escape(k) for k, _ in TITLE_MAP.items()], key=len, reverse=True)
combined = re.compile('(' + '|'.join(title_patterns) + r')|(第\s*(\d+)\s*课)')

# 排除：新6课中"第7课：安全代码操作"已是正确的新编号，不改
EXCLUDE_SUBSTR = "第7课：安全代码操作：执行隔离与原子编辑"

def replace_text(text):
    """单次扫描替换：先匹配标题，再匹配数字，标题优先防止二次映射。"""
    result = []
    pos = 0
    for m in combined.finditer(text):
        # 添加匹配前的文本
        result.append(text[pos:m.start()])
        pos = m.end()
        
        if m.group(1):  # 标题匹配
            old_title = m.group(1)
            new_title = TITLE_MAP.get(old_title, old_title)
            
            # 检查排除
            ctx = text[max(0, m.start()-20):m.end()+60]
            if EXCLUDE_SUBSTR in ctx:
                result.append(old_title)  # 不替换
            else:
                result.append(new_title)
        elif m.group(3):  # 纯数字匹配
            n = int(m.group(3))
            if n in NUM_MAP:
                old = m.group(0)
                new = old.replace(str(n), str(NUM_MAP[n]), 1)
                result.append(new)
            else:
                result.append(m.group(0))
    
    result.append(text[pos:])
    return ''.join(result)

# 处理所有文件
changed = []
for f in sorted(glob.glob(os.path.join(BOOK, "*.md"))):
    if f.endswith("README.md"):
        continue
    fname = os.path.basename(f)
    with open(f, encoding="utf-8") as fh:
        original = fh.read()
    new_text = replace_text(original)
    if new_text != original:
        changed.append(fname)
        with open(f, "w", encoding="utf-8") as fh:
            fh.write(new_text)
        print(f"✅ {fname}")

print(f"\n共修改 {len(changed)} 个文件")