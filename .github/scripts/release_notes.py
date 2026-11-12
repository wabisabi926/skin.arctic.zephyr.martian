#!/usr/bin/env python3
"""生成 GitHub Release 说明（Kodi 风格分类式，中文优先）。

数据源：addon.xml 的 <news> 段落（[B]版本[/B] + [CR]/换行分隔）。
输出：dist/release-notes.md，供 gh release create/edit --notes-file 使用。

规则：
  - 只取「上一个 Release 之后」的版本段 + 魔改段，不罗列全部历史版本
  - 按 新增 / 优化 / 修复 / 移除 归类；空分类不输出
  - 保留 PVR / OSD / fanart / 皮肤变量 等专有名词，中文优先，英文条目原样保留
  - 一条一行，去除营销化措辞与冗余前后缀

仅依赖标准库；gh CLI 只用于查询本分支上一个 Release（可选）。
本地调试：
    PREV_RELEASE_TAG=v4.1.7-with-poster-title-douban-Piers \
    TAG=v4.1.10-with-poster-title-douban-Piers \
    ZIP_NAME=skin.example.zip GITHUB_REPOSITORY=owner/repo \
    python3 .github/scripts/release_notes.py
"""

from __future__ import annotations

import os
import re
import subprocess
import sys

ADDON_XML = "addon.xml"
OUT_PATH = os.path.join("dist", "release-notes.md")
CUSTOM_SECTION = "魔改"

# GitHub Release Notes 风格：固定顺序，空分类跳过
CATEGORIES = ("新增", "优化", "修复", "移除")

# 首词决定分类。中文优先；英文条目按 Kodi 官方 changelog 用词兜底。
RULES: tuple[tuple[str, str], ...] = (
    # 中文前缀后不能加 \b：汉字属于 \w，后接汉字时词边界不成立。
    # \b 只加在英文分支内。
    (
        "移除",
        r"^(?:(?:移除|删除|去掉|弃用)|(?:Removed?|Deleted?|Dropped?|Deprecated?)\b)",
    ),
    (
        "修复",
        r"^(?:(?:修复|修正)|(?:Fix(?:ed)?|Bugfix|Hotfix|Corrected?)\b)",
    ),
    (
        "新增",
        r"^(?:(?:新增|增加|添加)|(?:Added?|New|Introduced?|Implemented?)\b)",
    ),
    (
        "优化",
        r"^(?:(?:优化|改进|调整|更新|更改|改用|切换|替换|扩展|简化|清理|补充|启用|禁用|支持|"
        r"允许|隐藏|显示|预加载)|(?:Updated?|Changed?|Improved?|Adjusted?|Extended?|"
        r"Simplified?|Cleanup|Code cleanup|Refactor(?:ed|ing)?|Pre-load|Hide|Show|"
        r"Include[sd]?|Allow(?:ed)?|Support(?:ed)?)\b)",
    ),
)

# 营销化 / 冗余措辞，输出前剔除
NOISE = (
    (r"（感谢\s*@[^）]+）", ""),
    (r"\(thanks\s*@[^)]+\)", ""),
    (r"[，,]?\s*显示更流畅$", ""),
    (r"[，,]?\s*更加流畅$", ""),
)


def sh(*args: str) -> str:
    """执行命令并返回 stdout，失败时返回空串。"""
    try:
        res = subprocess.run(args, capture_output=True, text=True, check=False)
    except OSError:
        return ""
    if res.returncode != 0:
        print(f"[warn] {' '.join(args)} -> {res.stderr.strip()}", file=sys.stderr)
        return ""
    return res.stdout.strip()


def read_addon() -> tuple[str, str]:
    with open(ADDON_XML, encoding="utf-8") as fh:
        raw = fh.read()
    id_match = re.search(r"<addon\b[^>]*\bid=\"([^\"]+)\"", raw)
    ver_match = re.search(r"<addon\b[^>]*\bversion=\"([^\"]+)\"", raw)
    if not id_match or not ver_match:
        sys.exit("addon.xml 中未找到 id/version 属性")
    return id_match.group(1), ver_match.group(1)


def read_news() -> str:
    with open(ADDON_XML, encoding="utf-8") as fh:
        raw = fh.read()
    match = re.search(r"<news>(.*?)</news>", raw, re.S)
    if not match:
        sys.exit("addon.xml 中未找到 <news> 段落")
    return match.group(1)


def parse_sections(news: str) -> list[dict]:
    """把 <news> 解析成 [{title, items}]，兼容 [CR] 单行与换行两种写法。"""
    text = news.replace("[CR]", "\n")
    text = re.sub(r"\[B\](.*?)\[/B\]", r"\n@@\1@@\n", text)

    sections: list[dict] = []
    current: dict | None = None
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        header = re.fullmatch(r"@@(.+?)@@", line)
        if header:
            current = {"title": header.group(1).strip(), "items": []}
            sections.append(current)
        elif current is not None:
            current["items"].append(line[2:].strip() if line.startswith("- ") else line)
    return [s for s in sections if s["items"]]


def version_key(title: str) -> tuple[int, ...] | None:
    if not re.fullmatch(r"\d+(?:\.\d+)*", title):
        return None
    return tuple(int(p) for p in title.split("."))


def tag_version(tag: str) -> tuple[int, ...] | None:
    found = re.findall(r"\d+(?:\.\d+)+", tag)
    if not found:
        return None
    return tuple(int(p) for p in found[-1].split("."))


def prev_release_tag(tag: str, branch: str) -> str:
    """本分支上一个已发布 Release 的 tag（按版本号取最大，避免跨分支误判）。"""
    explicit = os.environ.get("PREV_RELEASE_TAG", "").strip()
    if explicit:
        return explicit
    out = sh(
        "gh", "release", "list", "--limit", "50",
        "--json", "tagName", "--jq", ".[].tagName",
    )
    suffix = "-" + branch.replace("/", "-") if branch else ""
    candidates: list[tuple[tuple[int, ...], str]] = []
    for line in out.splitlines():
        candidate = line.strip()
        if not candidate or candidate == tag:
            continue
        if suffix and not candidate.endswith(suffix):
            continue
        key = tag_version(candidate)
        if key is not None:
            candidates.append((key, candidate))
    if not candidates:
        return ""
    return max(candidates)[1]


def tidy(item: str) -> str:
    """压缩空白、去掉项目符号与营销化措辞，保留技术术语。"""
    text = re.sub(r"^-\s*", "", item.strip())
    text = re.sub(r"\s+", " ", text).strip()
    for pattern, repl in NOISE:
        text = re.sub(pattern, repl, text, flags=re.I)
    return text.strip(" ，,;；")


def classify(item: str) -> str:
    for category, pattern in RULES:
        if re.match(pattern, item, re.I):
            return category
    return "优化"


def build_notes(version: str, tag: str, zip_name: str, branch: str) -> str:
    sections = parse_sections(read_news())
    cur_key = version_key(version)

    prev_tag = prev_release_tag(tag, branch)
    prev_key = tag_version(prev_tag) if prev_tag else None
    print(f"[info] 当前版本 {version} / 上一个 Release: {prev_tag or '(无)'}")

    buckets: dict[str, list[str]] = {c: [] for c in CATEGORIES}
    seen: set[str] = set()

    def add(items: list[str]) -> None:
        for raw in items:
            item = tidy(raw)
            if not item or item in seen:
                continue
            seen.add(item)
            buckets[classify(item)].append(item)

    # 魔改段：本分支相对上游的常驻变更，随每次发布一并列出
    for sec in sections:
        if sec["title"] == CUSTOM_SECTION:
            add(sec["items"])

    # 版本段：仅取上一 Release 之后的部分，不罗列全部历史
    versioned = [(version_key(s["title"]), s) for s in sections]
    versioned = [(k, s) for k, s in versioned if k is not None]
    if prev_key is None:
        picked = [(k, s) for k, s in versioned if k == cur_key][:1]
    else:
        picked = [(k, s) for k, s in versioned if k > prev_key]
        picked.sort(key=lambda kv: kv[0])
        if cur_key not in {k for k, _ in picked}:
            print(
                f"[warn] addon.xml <news> 中缺少 {version} 条目，建议补一条",
                file=sys.stderr,
            )
    for _, sec in picked:
        add(sec["items"])

    lines: list[str] = [f"# {version} 更新说明", ""]
    lines.append(
        f"同步上游 martian89/piers v{version}；下列含本分支魔改项（相对上游）。"
    )
    lines.append("")

    for category in CATEGORIES:
        if not buckets[category]:
            continue
        lines.append(f"## {category}")
        lines.append("")
        lines.extend(f"- {item}" for item in buckets[category])
        lines.append("")

    if not any(buckets.values()):
        lines.append("本次发布仅包含构建相关调整。")
        lines.append("")

    lines.append("## 安装")
    lines.append("")
    asset = f"`{zip_name}`" if zip_name else "zip"
    lines.append(f"- 下载 {asset}，在 Kodi 中「设置 → 附加组件 → 从 zip 文件安装」")
    lines.append("- 依赖魔改版 TMDbHelper 以启用豆瓣评分")
    lines.append("")

    repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if repo and prev_tag:
        lines.append(
            f"**完整变更**：https://github.com/{repo}/compare/{prev_tag}...{tag}"
        )
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def main() -> None:
    _, version = read_addon()
    tag = os.environ.get("TAG", f"v{version}")
    zip_name = os.environ.get("ZIP_NAME", "")
    branch = os.environ.get("BRANCH", "")
    notes = build_notes(version, tag, zip_name, branch)

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as fh:
        fh.write(notes)
    print(notes)


if __name__ == "__main__":
    main()
