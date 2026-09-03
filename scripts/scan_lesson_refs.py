# -*- coding: utf-8 -*-
"""扫描教程正文中所有「第 N 课」引用，分类输出，便于制定替换策略。"""
import os, re, glob

BOOK = "AI-Coding-Agent-Dummy-Book"

# 旧编号 -> 新编号（24 课方案）
MAPPING = {1:1,2:2,3:3,4:4,5:5,6:6,7:6,8:7,9:7,10:8,11:9,12:10,13:10,14:11,15:12,
           16:14,17:15,18:16,19:18,20:21,21:23,22:24}

pat = re.compile(r"第\s*(\d+)\s*课")

title_refs = []   # 带冒号的标题引用
plain_refs = []   # 纯编号引用
special = []      # 疑似特殊（历史叙述/课程总数）

for f in sorted(glob.glob(os.path.join(BOOK, "*.md"))):
    if f.endswith("README.md"):
        continue
    name = os.path.basename(f)
    with open(f, encoding="utf-8") as fh:
        lines = fh.read().split("\n")
    for i, line in enumerate(lines, 1):
        for m in pat.finditer(line):
            old = int(m.group(1))
            if old not in MAPPING:
                continue
            new = MAPPING[old]
            after = line[m.end():m.end()+3]
            # 标题引用：第 X 课：/第X课: 后跟冒号
            is_title = after.startswith("：") or after.startswith(":")
            entry = f"{name}:{i}: old={old} -> new={new} :: {line.strip()[:90]}"
            if is_title:
                title_refs.append(entry)
            elif "全套" in line or "从 19 课" in line or "从 22 课" in line or "到 24 课" in line:
                special.append(entry)
            else:
                plain_refs.append(entry)

print(f"===== 标题引用（{len(title_refs)} 处）=====")
for e in title_refs:
    print(e)
print(f"\n===== 纯编号引用（{len(plain_refs)} 处）=====")
for e in plain_refs:
    print(e)
print(f"\n===== 特殊（{len(special)} 处）=====")
for e in special:
    print(e)
