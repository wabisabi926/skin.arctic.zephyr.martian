#!/usr/bin/env python3
"""根据 addon.xml 的 <news> 段落生成中文 Release 说明。

只依赖标准库 + gh CLI（用于查询上一个 Release 的版本号）。
输出写入 dist/release-notes.md，供 gh release create/edit --notes-file 使用。

用法（本地调试）：
    PREV_RELEASE_TAG=with-poster-title-douban-Piers-4.1.1 \
    TAG=v4.1.5-with-poster-title-douban-Piers \
    ZIP_NAME=xxx.zip \
    GITHUB_REPOSITORY=owner/repo \
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
CUSTOM_TITLE = "本分支魔改"


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
    """把 [B]标题[/B] 与 [CR] 分隔的新闻正文解析成 [{title, items}]，保持原有顺序。"""
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
    """标题是纯版本号（如 4.1.5）时返回可比较的元组，否则返回 None。"""
    if not re.fullmatch(r"\d+(?:\.\d+)*", title):
        return None
    return tuple(int(p) for p in title.split("."))


def prev_release_tag(current_tag: str) -> str:
    """上一个已发布 Release 的 tag（按发布时间倒序取第一个非当前 tag）。"""
    explicit = os.environ.get("PREV_RELEASE_TAG", "").strip()
    if explicit:
        return explicit
    out = sh(
        "gh", "release", "list", "--limit", "30",
        "--json", "tagName", "--jq", ".[].tagName",
    )
    for tag in out.splitlines():
        tag = tag.strip()
        if tag and tag != current_tag:
            return tag
    return ""


def prev_version(prev_tag: str) -> tuple[int, ...] | None:
    found = re.findall(r"(\d+(?:\.\d+)+)", prev_tag)
    if not found:
        return None
    return tuple(int(p) for p in found[-1].split("."))


def build_notes(addon_id: str, version: str, tag: str, zip_name: str) -> str:
    sections = parse_sections(read_news())
    cur_key = version_key(version)

    prev_tag = prev_release_tag(tag)
    prev_key = prev_version(prev_tag) if prev_tag else None
    print(f"[info] 当前版本 {version} / 上一个 Release: {prev_tag or '(无)'}")

    custom = [s for s in sections if s["title"] == CUSTOM_SECTION]
    versioned = [(version_key(s["title"]), s) for s in sections]
    versioned = [(k, s) for k, s in versioned if k is not None]

    if prev_key is None:
        # 没有可比对的历史版本：只输出当前版本 + 魔改
        picked = [(k, s) for k, s in versioned if k == cur_key][:1]
    else:
        picked = [(k, s) for k, s in versioned if k > prev_key]
        picked.sort(key=lambda kv: kv[0], reverse=True)
        if cur_key not in {k for k, _ in picked}:
            print(
                f"[warn] addon.xml <news> 中缺少 {version} 的条目，"
                "建议补一条以便 Release 说明准确",
                file=sys.stderr,
            )

    lines: list[str] = [f"# {version} 更新说明", ""]
    lines.append(
        f"同步上游 martian89 的 v{version} 更新，并保留本分支的豆瓣评分、海报墙标题等魔改功能。"
    )
    lines.append("")

    if custom:
        lines.append(f"## {CUSTOM_TITLE}")
        lines.append("")
        lines.extend(f"- {item}" for item in custom[0]["items"])
        lines.append("")

    for key, sec in picked:
        title = f"## 同步上游 {sec['title']}"
        if key == cur_key:
            title += "（本版）"
        lines.append(title)
        lines.append("")
        lines.extend(f"- {item}" for item in sec["items"])
        lines.append("")

    if not picked and not custom:
        lines.append("本次发布仅包含构建相关的调整。")
        lines.append("")

    lines.append("## 下载安装")
    lines.append("")
    if zip_name:
        lines.append(f"- 下载下方 `{zip_name}`，在 Kodi 中「设置 → 附加组件 → 从 zip 文件安装」即可")
    else:
        lines.append("- 下载下方 zip，在 Kodi 中「设置 → 附加组件 → 从 zip 文件安装」即可")
    lines.append("- 升级前建议先备份 `userdata/addon_data/skin.arctic.zephyr.martian/` 里的个性化设置")
    lines.append("- 豆瓣评分需搭配魔改版 TMDbHelper 使用")
    lines.append("")

    repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if repo and prev_tag:
        compare = f"https://github.com/{repo}/compare/{prev_tag}...{tag}"
        lines.append(f"**完整对比**：{compare}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def main() -> None:
    addon_id, version = read_addon()
    tag = os.environ.get("TAG", f"v{version}")
    zip_name = os.environ.get("ZIP_NAME", "")
    notes = build_notes(addon_id, version, tag, zip_name)

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as fh:
        fh.write(notes)
    print(notes)


if __name__ == "__main__":
    main()
