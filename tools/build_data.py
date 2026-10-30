# -*- coding: utf-8 -*-
"""把纯文本小说转换成站点可直接加载的分章数据（支持多本书）。

书单配置：books/books.json
   [
     {
       "id": "gu-zhen-ren",                 必填，英文/数字/连字符，决定链接 #/b/<id>/<章>
       "title": "蛊真人",                   必填，书名
       "author": "蛊真人",                  可选，作者
       "tags": ["仙侠", "完本"],            可选，书架上的标签
       "note": "错别字校对 5.0 版",         可选，副标题（卡片上的一行小字）
       "intro": "一句话简介",               可选，书籍简介
       "source": "books/xxx.txt",           必填，原文路径（相对仓库根目录，也可写绝对路径）
       "download": "books/xxx.txt"          可选，供“下载 TXT”使用的文件；
                                                 与 source 相同 → 不复制；不同 → 自动复制一份
     },
     ...
   ]

输出：
  data/catalog.js          —— window.MD_PUBLIC_BOOKS，包含所有公开书的目录
  data/<id>/c/NNNN.js      —— 每章正文，格式 __MDC("<chapter-id>", ["段落", ...])

用法：
  python tools/build_data.py            # 重建全部
  python tools/build_data.py <id> ...   # 只重写指定书的章节文件（catalog 仍会全量重建）
"""
import json
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join(ROOT, "books", "books.json")

RE_VOLUME = re.compile(r"^(?:正文\s*)?第\s*([一二三四五六七八九十百零两0-9]{1,4})\s*卷$")
RE_SECTION = re.compile(r"^(?:正文\s*)?第\s*([一二三四五六七八九十百千万零两0-9]{1,6})\s*[节回章]\s*[：:、·.]?\s*(.*)$")
RE_EXTRA = re.compile(r"^(番外|序章|序言|楔子|引子|尾声|终章|后记|外传|附录)")
RE_PREFACE = re.compile(r"^(序|自序|前言|引子|楔子|写在前面)[：:，,\s]")
RE_ID = re.compile(r"^[a-z0-9][a-z0-9-]*$")
# 盗版站点水印 / 广告行（短行才丢，避免误伤正文）
RE_JUNK = re.compile(r"(最新网址|最新章节|请记住本站|小说网|笔趣阁|天才一秒记住|手机阅读|手机版|txt下载|"
                     r"加入书签|返回目录|一秒记住|www\.|http://|https://|\.com|\.net|\.org)")
# 句子终止符：不以这些结尾的行视为被折行的续行，与下一行合并
END_CHARS = "。！？…”’」』）】》!?."


class BookError(Exception):
    pass


def read_text(path):
    with open(path, "rb") as f:
        raw = f.read()
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def is_junk(line):
    return len(line) <= 60 and bool(RE_JUNK.search(line))


def norm_lines(text, drop_junk=True):
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    out = []
    for line in text.split("\n"):
        line = line.replace("\u3000", " ").strip()
        if line.endswith("\u200b"):
            line = line[:-1].strip()
        if drop_junk and line and is_junk(line):
            continue
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


def clean_title(t):
    """去掉「正文 」这类前缀与水印尾巴。"""
    t = re.sub(r"^正文[\s\u3000]*", "", t or "")
    t = re.sub(r"[\s\u3000]+$", "", t)
    return t.strip()


# ---------------------------------------------------------------- EPUB 解析
OPF_NS = "{http://www.idpf.org/2007/opf}"
NCX_NS = "{http://www.daisy.org/z3986/2005/ncx/}"
SKIP_NAV = {"封面", "目录", "版权信息", "纸质版编目数据", "Cover", "扉页", "书名页", "关于本书"}
VOL_NAV = re.compile(r"^第\s*[一二三四五六七八九十百零两0-9]{1,4}\s*[卷册部]")


def _clean_text(s):
    return re.sub(r"[\s\u00a0\u200b]+", " ", s or "").strip()


def _doc_paragraphs(raw):
    from lxml import html as LH
    doc = LH.document_fromstring(raw)
    for bad in doc.xpath("//script | //style"):
        bad.getparent().remove(bad)
    ps = [_clean_text(el.text_content()) for el in doc.iter("p")]
    ps = [p for p in ps if p]
    if not ps:
        whole = _clean_text(doc.text_content())
        ps = [whole] if whole else []
    return ps


def parse_epub(path):
    """按 NCX 目录切章：父节点=卷，叶子=章。返回 (chapters, volumes, notice)。"""
    import zipfile
    from lxml import etree

    z = zipfile.ZipFile(path)
    container = z.read("META-INF/container.xml")
    m = re.search(rb'full-path="([^"]+)"', container)
    if not m:
        raise BookError("EPUB 缺少 container.xml 中的 full-path")
    opf_path = m.group(1).decode("utf-8")
    base = opf_path.rsplit("/", 1)[0] + "/" if "/" in opf_path else ""
    opf = etree.fromstring(z.read(opf_path))

    manifest = {it.get("id"): it.get("href") for it in opf.iter(OPF_NS + "item")}
    spine = [r.get("idref") for r in opf.iter(OPF_NS + "itemref")]
    files = [manifest[s] for s in spine if manifest.get(s)]
    file_idx = {f: i for i, f in enumerate(files)}

    ncx_href = [it.get("href") for it in opf.iter(OPF_NS + "item")
                if it.get("media-type") == "application/x-dtbncx+xml"]
    if not ncx_href:
        raise BookError("EPUB 没有 NCX 目录，暂不支持")
    ncx = etree.fromstring(z.read(base + ncx_href[0]))

    nav = []

    def walk(node, depth):
        for np_ in node.findall(NCX_NS + "navPoint"):
            label = _clean_text(np_.findtext(NCX_NS + "navLabel/" + NCX_NS + "text"))
            src = (np_.find(NCX_NS + "content").get("src") or "").split("#")[0]
            kids = np_.findall(NCX_NS + "navPoint")
            if label and src in file_idx:
                nav.append({"label": label, "file": src, "depth": depth, "kids": len(kids)})
            walk(np_, depth + 1)

    walk(ncx.find(NCX_NS + "navMap"), 0)
    if not nav:
        raise BookError("EPUB 目录为空")

    chapters, volumes = [], []
    cur_vol = -1
    cache = {}

    for i, ent in enumerate(nav):
        start = file_idx[ent["file"]]
        nxt = file_idx[nav[i + 1]["file"]] if i + 1 < len(nav) else len(files)
        if nxt <= start:                      # 与下一条目指向同一文件
            continue
        label = ent["label"]

        if ent["kids"] or VOL_NAV.match(label):
            volumes.append({"title": label, "start": len(chapters)})
            cur_vol = len(volumes) - 1
            continue
        if label in SKIP_NAV:
            continue

        paras = []
        for f in files[start:nxt]:
            if f not in cache:
                try:
                    cache[f] = _doc_paragraphs(z.read(base + f))
                except KeyError:
                    cache[f] = []
            paras.extend(cache[f])
        if not paras:
            continue

        # 去掉正文开头重复的标题行（中文标题 + 可能的英文副标题）
        lab = label.replace(" ", "")
        acc = ""
        for k in range(min(4, len(paras))):
            acc += paras[k].replace(" ", "")
            if acc == lab:
                paras = paras[k + 1:]
                break
            if not lab.startswith(acc):
                break
        for _ in range(3):                     # 紧跟的英文副标题
            if paras and paras[0].isascii() and len(paras[0]) < 60:
                paras = paras[1:]
            else:
                break

        if not paras:
            continue
        total = sum(len(p) for p in paras)
        if total < 20 and label not in ("内容介绍", "内容简介", "简介"):
            continue
        title = "简介" if label in ("内容介绍", "内容简介") else label
        chapters.append({"title": title, "paras": paras, "vol": cur_vol})

    if not chapters:
        raise BookError("EPUB 没有解析出章节正文")
    return chapters, volumes, ""


def parse_book_file(path):
    """按扩展名选择解析器，返回 (chapters, volumes, notice)。"""
    if path.lower().endswith(".epub"):
        return parse_epub(path)
    return parse_book(read_text(path))


def chapter_plain_text(chapters):
    out = []
    for c in chapters:
        out.append(c["title"])
        out.append("")
        out.extend(c["paras"])
        out.append("")
        out.append("")
    return "\n".join(out)


def parse_book(text):
    """返回 (chapters, volumes, notice)。chapters: [{title, paras, vol}]"""
    lines = norm_lines(text)

    # ---- 定位所有标题行 ----
    heads = []
    for i, line in enumerate(lines):
        if not line or not is_heading(line):
            continue
        vm = RE_VOLUME.match(line)
        if vm:
            # 卷名可能写在同一行，也可能在下一行
            name = line[vm.end():].strip()
            if not name and i + 1 < len(lines) and lines[i + 1] and not is_heading(lines[i + 1]):
                name = lines[i + 1]
            heads.append({"i": i, "kind": "vol",
                          "title": clean_title(line + (" " + name if name else "")),
                          "skip": 1 if (name and name != line) else 0})
            continue
        if RE_SECTION.match(line) and not RE_EXTRA.match(line) and len(line) <= 40:
            heads.append({"i": i, "kind": "sec", "title": clean_title(line), "skip": 0})
            continue
        heads.append({"i": i, "kind": "extra", "title": clean_title(line), "skip": 0})

    # 跳过被当作卷名吃掉的那一行
    for h in heads:
        if h["skip"]:
            h["i"] += 1

    # ---- 前言部分（第一处标题之前的内容）----
    chapters = []
    volumes = []

    first = heads[0]["i"] if heads else len(lines)
    front_joined = [l for l in lines[:first] if l]
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
        body_chunks.append((h, [l for l in chunk[drop:] if l]))

    cur_vol = -1
    for h, body in body_chunks:
        if h["kind"] == "vol":
            volumes.append({"title": h["title"], "start": len(chapters)})
            cur_vol = len(volumes) - 1
            continue
        if not body:
            continue
        chapters.append({"title": h["title"], "paras": merge_paragraphs(body), "vol": cur_vol})

    return chapters, volumes, "　".join(notice).strip()


def rel(path):
    """统一成 / 分隔的相对路径（用于写进 catalog 的 URL）"""
    path = path.replace("\\", "/")
    return path


def resolve(path):
    return path if os.path.isabs(path) else os.path.join(ROOT, path)


def load_config():
    if not os.path.exists(CONFIG):
        raise BookError("找不到书单配置：%s" % CONFIG)
    with open(CONFIG, encoding="utf-8-sig") as f:
        cfg = json.load(f)
    if not isinstance(cfg, list) or not cfg:
        raise BookError("books/books.json 需要是一个非空数组，例如 [ { \"id\": \"...\", ... } ]")

    seen = set()
    for i, b in enumerate(cfg):
        for field in ("id", "title", "source"):
            if not b.get(field):
                raise BookError("第 %d 条书单缺少字段：%s" % (i + 1, field))
        if not RE_ID.match(b["id"]):
            raise BookError("id 只能用小写字母、数字和连字符：%s（例：dou-po-cang-qiong）" % b["id"])
        if b["id"] in seen:
            raise BookError("id 重复：%s" % b["id"])
        seen.add(b["id"])
        if not os.path.exists(resolve(b["source"])):
            raise BookError("《%s》的原文不存在：%s" % (b["title"], b["source"]))
    return cfg


def build_book(b, write_files=True):
    src = resolve(b["source"])
    chapters, volumes, notice = parse_book_file(src)
    if not chapters:
        raise BookError("《%s》没有解析出任何章节" % b["title"])

    out_rel = b.get("dir") or os.path.join("data", b["id"], "c")
    out_dir = os.path.join(ROOT, out_rel)
    os.makedirs(out_dir, exist_ok=True)

    index = []
    for n, c in enumerate(chapters):
        fid = "%04d" % (n + 1)
        paras = [p for p in c["paras"] if p.strip()]
        if write_files:
            payload = "__MDC(%s,%s);\n" % (json.dumps(fid), json.dumps(paras, ensure_ascii=False))
            with open(os.path.join(out_dir, fid + ".js"), "w", encoding="utf-8", newline="\n") as f:
                f.write(payload)
        index.append({"t": c["title"], "f": fid, "w": sum(len(p) for p in paras), "v": c["vol"]})

    # 清掉旧的多余章节文件
    keep = set(x["f"] + ".js" for x in index)
    for old in os.listdir(out_dir):
        if old.endswith(".js") and old not in keep:
            os.remove(os.path.join(out_dir, old))

    # 原文副本（供“下载 TXT”）
    txt_url = ""
    download = b.get("download") or b["source"]
    dl_path = resolve(download)
    if os.path.abspath(dl_path) != os.path.abspath(src):
        os.makedirs(os.path.dirname(dl_path) or ROOT, exist_ok=True)
        if src.lower().endswith(".epub"):
            # EPUB 直接导出为纯文本，便于“下载 TXT”
            with open(dl_path, "w", encoding="utf-8", newline="\n") as d:
                d.write(chapter_plain_text(chapters))
        else:
            with open(src, "rb") as s, open(dl_path, "wb") as d:
                d.write(s.read())
    if os.path.exists(dl_path) and os.path.abspath(dl_path).startswith(ROOT):
        txt_url = rel(os.path.relpath(dl_path, ROOT))

    entry = {
        "id": b["id"],
        "title": b["title"],
        "author": b.get("author", ""),
        "tags": b.get("tags", []),
        "note": b.get("note", ""),
        "notice": notice,
        "intro": b.get("intro", ""),
        "chapters": index,
        "volumes": volumes,
        "words": sum(c["w"] for c in index),
        "dir": rel(out_rel) + "/",
        "txt": txt_url,
        "public": True,
        "updatedAt": 0,
    }
    return entry


def main():
    t0 = time.time()
    only = set(sys.argv[1:])
    try:
        cfg = load_config()
    except BookError as e:
        print("配置错误：", e)
        sys.exit(1)

    catalog = []
    for b in cfg:
        try:
            entry = build_book(b, write_files=(not only or b["id"] in only))
        except BookError as e:
            print("跳过《%s》：%s" % (b.get("title", "?"), e))
            continue
        catalog.append(entry)
        print("✓ %-14s %-10s %5d 章  %9d 字  %2d 卷" % (
            entry["id"], entry["title"], len(entry["chapters"]), entry["words"], len(entry["volumes"])))

    if not catalog:
        print("没有任何书被生成，已中止。")
        sys.exit(1)

    os.makedirs(os.path.join(ROOT, "data"), exist_ok=True)
    with open(os.path.join(ROOT, "data", "catalog.js"), "w", encoding="utf-8", newline="\n") as f:
        f.write("/* 由 tools/build_data.py 自动生成，请勿手改 */\n")
        f.write("window.MD_PUBLIC_BOOKS = ")
        f.write(json.dumps(catalog, ensure_ascii=False, separators=(",", ":")))
        f.write(";\n")

    print("\n共 %d 本公开书，总 %d 字，耗时 %.1fs" % (
        len(catalog), sum(b["words"] for b in catalog), time.time() - t0))
    print("下一步：git add -A && git commit -m \"新增书籍\" && git push（Pages 会自动重建，约 1 分钟）")


if __name__ == "__main__":
    main()
