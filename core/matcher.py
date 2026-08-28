"""关键词匹配引擎：标题 × 关键词（any/all、正则开关、大小写）。"""
from __future__ import annotations

import re


def match_keywords(title: str, keywords: list[dict], match_logic: str = "any") -> list[str]:
    """返回命中的关键词文本列表；未命中返回 []。

    keywords 元素: {"text": str, "regex": bool, "case_sensitive": bool}
    match_logic: "any" 任一命中即返回；"all" 需全部关键词命中。
    """
    if not keywords:
        return []
    title = title or ""
    hits: list[str] = []
    for kw in keywords:
        text = str(kw.get("text", "")).strip()
        if not text:
            continue
        regex = bool(kw.get("regex", False))
        cs = bool(kw.get("case_sensitive", False))
        if regex:
            flags = 0 if cs else re.IGNORECASE
            try:
                ok = re.search(text, title, flags) is not None
            except re.error:
                ok = text in title  # 非法正则降级为子串
        else:
            ok = (text in title) if cs else (text.lower() in title.lower())
        if ok:
            hits.append(text)
    if match_logic == "all":
        valid = [k["text"] for k in keywords if str(k.get("text", "")).strip()]
        if len(hits) != len(valid):
            return []
    return hits


def is_excluded(title: str, exclude_keywords: list[dict] | None) -> bool:
    """标题命中任一排除关键词（子串/正则，大小写规则同包含词）即视为排除。"""
    if not exclude_keywords:
        return False
    title = title or ""
    for kw in exclude_keywords:
        text = str(kw.get("text", "")).strip()
        if not text:
            continue
        regex = bool(kw.get("regex", False))
        cs = bool(kw.get("case_sensitive", False))
        if regex:
            flags = 0 if cs else re.IGNORECASE
            try:
                if re.search(text, title, flags) is not None:
                    return True
            except re.error:
                if text.lower() in title.lower():
                    return True
        else:
            if (text in title) if cs else (text.lower() in title.lower()):
                return True
    return False
