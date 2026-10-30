# -*- coding: utf-8 -*-
"""把纯文本小说转换成站点可直接加载的分章数据。

输入：纯文本小说（UTF-8 / 带 BOM 均可）
输出：
  data/catalog.js       —— window.MD_PUBLIC_BOOKS，含书名、简介、章节目录
  data/gu/c/NNNN.js     —— 每章正文，格式 __MDC("<id>", ["段落", ...])
  data/gu/<raw>.txt     —— 原文副本（供下载）

用法：
  python tools/build_data.py "源文件.txt"
"""
import json
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = sys.argv[1] if len(sys.argv) > 1 else r"D:\mdu-reading\蛊真人无删减错别字修正5.0.txt"

BOOK_ID = "gu-zhen-ren"
BOOK_TITLE = "蛊真人"
BOOK_AUTHOR = "蛊真人"
BOOK_TAGS = ["仙侠", "黑暗流", "完本"]
BOOK_NOTE = "错别字校对 5.0 版 · 章节已重排"

RE_VOLUME = re.compile(r"^第\s*([一二三四五六七八九十百零两0-9]{1,4})\s*卷$")
RE_SECTION = re.compile(r"^第\s*([一二三四五六七八九十百千万零两0-9]{1,6})\s*[节回]\s*[：:、·.]?\s*(.*)$")
RE_EXTRA = re.compile(r"^(番外|序章|序言|楔子|引子|尾声|终章|后记|外传|附录)")
RE_PREFACE = re.compile(r"^(序|自序|前言|引子|楔子|写在前面)[：:，,\s]")
# 句子终止符：不以这些结尾的行视为被折行的续行，与下一行合并
END_CHARS = "。！？…”’」』）】》!?."


def read_text(path):
    with open(path, "rb") as f:
        raw = f.read()
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def norm_lines(text):
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    out = []
    for line in text.split("\n"):
        line = line.replace("\u3000", " ").strip()
        if line.endswith("\u200b"):
            line = line[:-1].strip()
        out.append(line)
    return out


def merge_paragraphs(lines):
    """把被硬折行的行合并回段落。原文绝大多数行本身就是完整段落。"""
    paras, buf = [], ""
    for line in lines:
        if not line:
            if buf:
                paras.append(buf)
                buf = ""
            continue
        buf = buf + line if buf else line
        if line[-1] in END_CHARS:
            paras.append(buf)
            buf = ""
    if buf:
        paras.append(buf)
    return paras


def is_heading(line):
    return bool(RE_VOLUME.match(line) or RE_SECTION.match(line) or RE_EXTRA.match(line))


def main():
    t0 = time.time()
    text = read_text(SRC)
    lines = norm_lines(text)
    print("源文件行数:", len(lines))

    # ---- 定位所有标题行 ----
    heads = []
    for i, line in enumerate(lines):
        if not line or not is_heading(line):
            continue
        m = RE_SECTION.match(line)
        tag = "sec" if (m and not RE_EXTRA.match(line)) else ""
        vm = RE_VOLUME.match(line)
        if vm:
            # 卷名可能写在同一行，也可能在下一行（本书是下一行）
            name = line[vm.end():].strip()
            if not name and i + 1 < len(lines) and lines[i + 1] and not is_heading(lines[i + 1]):
                name = lines[i + 1]
            heads.append({"i": i, "kind": "vol", "title": (line + (" " + name if name else "")).strip(),
                          "skip": 1 if (name and name != line) else 0})
            continue
        if tag:
            heads.append({"i": i, "kind": "sec", "title": line, "skip": 0})
            continue
        heads.append({"i": i, "kind": "extra", "title": line, "skip": 0})

    # 跳过被当作卷名吃掉的那一行
    for h in heads:
        if h["skip"]:
            h["i"] += 1

    # ---- 前言部分（第一处标题之前的内容）----
    chapters = []          # [{title, paras, vol}]
    volumes = []           # [{title, start}]

    first = heads[0]["i"] if heads else len(lines)
    front_joined = [l for l in lines[:first] if l]
    notice = []
    # 前几行：书名 / 作者 / 校对说明 … / 简介 / 序
    intro_idx = pref_idx = None
    for k, l in enumerate(front_joined):
        if intro_idx is None and l in ("简介", "内容简介", "作品简介"):
            intro_idx = k
            continue
        if intro_idx is not None and pref_idx is None and RE_PREFACE.match(l):
            pref_idx = k
    if intro_idx is None:                      # 没有“简介”标记时，退化为前几行
        intro_idx, intro_paras = 0, front_joined[1:3]
    else:
        intro_paras = front_joined[intro_idx + 1: pref_idx if pref_idx is not None else len(front_joined)]
    notice = front_joined[2:intro_idx] if intro_idx > 2 else []

    if intro_paras:
        chapters.append({"title": "简介", "paras": merge_paragraphs(intro_paras), "vol": -1})

    if pref_idx is not None:
        head = front_joined[pref_idx]
        body_start = pref_idx + 1
        # 标题被折断成两行的情况：下一行很短且没有句末标点 → 并入标题
        if body_start < len(front_joined):
            nxt = front_joined[body_start]
            if len(nxt) <= 24 and nxt[-1] not in END_CHARS and not RE_PREFACE.match(nxt):
                head = head + nxt
                body_start += 1
        while head and head[-1] in "，,、 ":
            head = head[:-1]
        chapters.append({"title": head.strip(),
                         "paras": merge_paragraphs(front_joined[body_start:]),
                         "vol": -1})

    # ---- 按标题切分正文 ----
    body_chunks = []
    for n, h in enumerate(heads):
        start = h["i"]
        end = heads[n + 1]["i"] if n + 1 < len(heads) else len(lines)
        chunk = lines[start:end]
        # 去掉标题行本身；卷标题行同时去掉紧随的卷名行
        drop = 1
        if h["kind"] == "vol" and len(chunk) > 1 and chunk[1] and \
                chunk[1] in h["title"] and chunk[1] != chunk[0]:
            drop = 2
        body = [l for l in chunk[drop:] if l]
        body_chunks.append((h, body))

    cur_vol = -1
    for h, body in body_chunks:
        if h["kind"] == "vol":
            volumes.append({"title": h["title"], "start": len(chapters)})
            cur_vol = len(volumes) - 1
            continue
        if not body:
            continue
        title = h["title"]
        chapters.append({"title": title, "paras": merge_paragraphs(body), "vol": cur_vol})

    # ---- 报告 ----
    words = sum(len("".join(c["paras"])) for c in chapters)
    print("章节数:", len(chapters), "总字数:", words)
    print("卷:", [v["title"] for v in volumes])
    for vi, v in enumerate(volumes):
        nxt = volumes[vi + 1]["start"] if vi + 1 < len(volumes) else len(chapters)
        print("  %-12s 章节 %d" % (v["title"], nxt - v["start"]))
    print("首章:", chapters[0]["title"])
    print("末章:", chapters[-1]["title"])

    # ---- 写数据 ----
    out_c = os.path.join(ROOT, "data", "gu", "c")
    os.makedirs(out_c, exist_ok=True)
    for old in os.listdir(out_c):
        if old.endswith(".js"):
            os.remove(os.path.join(out_c, old))

    index = []
    for n, c in enumerate(chapters):
        fid = "%04d" % (n + 1)
        paras = [p for p in c["paras"] if p.strip()]
        payload = "__MDC(%s,%s);\n" % (json.dumps(fid), json.dumps(paras, ensure_ascii=False))
        with open(os.path.join(out_c, fid + ".js"), "w", encoding="utf-8", newline="\n") as f:
            f.write(payload)
        index.append({"t": c["title"], "f": fid, "w": sum(len(p) for p in paras), "v": c["vol"]})

    catalog = [{
        "id": BOOK_ID,
        "title": BOOK_TITLE,
        "author": BOOK_AUTHOR,
        "tags": BOOK_TAGS,
        "note": BOOK_NOTE,
        "notice": "　".join(notice).strip(),
        "intro": "人是万物之灵，蛊是天地真精。三观不正，魔头重生。一个穿越者不断重生的故事，"
                 "一个养蛊、炼蛊、用蛊的奇特世界，还有一个纵情纵横的绝世大魔头。",
        "chapters": index,
        "volumes": volumes,
        "words": words,
        "dir": "data/gu/c/",
        "txt": "data/gu/gu-zhen-ren.txt",
        "public": True,
        "updatedAt": 0,
    }]

    os.makedirs(os.path.join(ROOT, "data"), exist_ok=True)
    with open(os.path.join(ROOT, "data", "catalog.js"), "w", encoding="utf-8", newline="\n") as f:
        f.write("/* 由 tools/build_data.py 自动生成，请勿手改 */\n")
        f.write("window.MD_PUBLIC_BOOKS = ")
        f.write(json.dumps(catalog, ensure_ascii=False, separators=(",", ":")))
        f.write(";\n")

    # 原文副本
    dst = os.path.join(ROOT, "data", "gu", "gu-zhen-ren.txt")
    with open(SRC, "rb") as src, open(dst, "wb") as o:
        o.write(src.read())

    print("输出目录:", out_c)
    print("耗时 %.1fs" % (time.time() - t0))


if __name__ == "__main__":
    main()
