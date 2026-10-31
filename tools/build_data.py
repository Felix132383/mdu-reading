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


RE_SECTION_HEAD = re.compile(r"^(?:正文\s*)?第\s*[一二三四五六七八九十百千万零两0-9]{1,6}\s*[节回章](.*)$")


def is_heading(line, loose=False):
    """判断某一行是不是章节标题。

    关键点：像「第二章的正文内容。」这种句子，章号后面直接跟正文、没有分隔符，
    不能当标题（否则会把整章吞掉）。真正的标题要么到此为止（「第二百零六节：」），
    要么用空格 / 冒号 / 顿号隔开（「第一章 开端」「第一章：开端」）。
    """
    if not line or len(line) > 40:
        return False

    if RE_VOLUME.match(line) or RE_EXTRA.match(line):
        # 序章 / 番外 / 楔子 这类标记较强，但整句带标点的仍是正文
        return not (line[-1] in "。，、；" and len(line) > 12)

    m = RE_SECTION_HEAD.match(line)
    if not m:
        return False
    raw = m.group(1)
    if not raw.strip():
        return True
    if raw[0] in " \u3000：:、·.-—":      # 有分隔符 → 一定是标题（标题可能以 ！？ 结尾）
        return True
    tail = raw.strip()
    # 没有分隔符（「第两百一十一章天作之合」）：短、不以句末标点结尾、不以虚词开头 → 仍是标题
    if len(tail) <= 12 and tail[-1] not in "。！？…" and tail[0] not in "的了是在有就都也这那我你他她它":
        return True
    return loose


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


def docx_lines(path):
    """读取 .docx 的段落文本（忽略图片、表格、样式）。"""
    try:
        from docx import Document
    except ImportError:
        raise BookError("解析 .docx 需要 python-docx：pip install python-docx")
    doc = Document(path)
    out = []
    for p in doc.paragraphs:
        t = (p.text or "").strip()
        if t:
            out.append(t)
    for table in doc.tables:                 # 少数 txt 转 docx 会把正文塞进表格
        for row in table.rows:
            for cell in row.cells:
                t = (cell.text or "").strip()
                if t:
                    out.append(t)
    return out


def md_lines(path):
    """读取 .md，去掉井号标记与代码围栏，保留正文。"""
    lines = []
    for line in read_text(path).replace("\r\n", "\n").split("\n"):
        s = line.strip()
        if s.startswith("```"):
            continue
        if s.startswith("#"):
            s = s.lstrip("#").strip()
            if not s:
                continue
        s = re.sub(r"^\s*[-*+]\s+", "", s)
        s = re.sub(r"\*\*(.+?)\*\*", r"\1", s)
        lines.append(s)
    return lines


def natural_key(name):
    """1.txt < 2.txt < 10.txt 这种自然序，方便按章连载。"""
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", name)]


def source_files(src):
    """source 可以是单个文件，也可以是一个目录（目录里的文件按自然序拼接成一本书）。"""
    if os.path.isdir(src):
        names = [n for n in os.listdir(src)
                 if not n.startswith((".", "~$")) and n.lower().endswith(BOOK_EXT)]
        names.sort(key=natural_key)
        files = [os.path.join(src, n) for n in names]
        if not files:
            raise BookError("目录里没有可用的书籍文件：%s" % src)
        return files
    if not os.path.exists(src):
        raise BookError("原文不存在：%s" % src)
    return [src]


def lines_of(path):
    low = path.lower()
    if low.endswith(".docx"):
        return [l.replace("\u3000", " ").strip() for l in docx_lines(path)]
    if low.endswith(".md"):
        return [l.replace("\u3000", " ").strip() for l in md_lines(path)]
    if low.endswith(".epub"):
        raise BookError("目录里请放 txt / md / docx；epub 请单独作为一本书")
    return norm_lines(read_text(path))


def parse_book_file(path):
    """返回 (chapters, volumes, notice)。

    path 是文件 → 单文件解析；path 是目录 → 目录内所有文件按自然序拼起来，
    当成一本「分章上传」的书（连载时每次只加一个文件即可）。
    """
    files = source_files(path)
    if len(files) == 1 and files[0].lower().endswith(".epub"):
        return parse_epub(files[0])

    lines = []
    for f in files:
        lines.extend(lines_of(f))
        lines.append("")

    chapters, volumes, notice = parse_book_lines(lines)
    # 章节太少但正文很长 → 多半是「第一章开端」这种标题没有分隔符，放宽再解析一次
    if len(chapters) < 3 and sum(len(l) for l in lines) > 20000:
        loose = parse_book_lines(lines, loose=True)
        if len(loose[0]) > len(chapters):
            print("  （标题无分隔符，已按宽松模式重新切分：%d 章）" % len(loose[0]))
            return loose
    return chapters, volumes, notice


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
    return parse_book_lines(norm_lines(text))


def parse_book_lines(lines, loose=False):
    """lines 已做过空白归一，正文/标题切分都从这里开始。"""
    # ---- 定位所有标题行 ----
    heads = []
    for i, line in enumerate(lines):
        if not line or not is_heading(line, loose):
            continue
        vm = RE_VOLUME.match(line)
        if vm:
            # 卷名可能写在同一行，也可能在下一行
            name = line[vm.end():].strip()
            if not name and i + 1 < len(lines) and lines[i + 1] and not is_heading(lines[i + 1], loose):
                name = lines[i + 1]
            heads.append({"i": i, "kind": "vol",
                          "title": clean_title(line + (" " + name if name else "")),
                          "skip": 1 if (name and name != line) else 0})
            continue
        if RE_SECTION.match(line) and not RE_EXTRA.match(line):
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
    if intro_idx is None:                      # 没有“简介”标记时，取书名/作者行之后的前几行
        intro_idx, intro_paras = 0, front_joined[1:7]
    else:
        intro_paras = front_joined[intro_idx + 1: pref_idx if pref_idx is not None else len(front_joined)]
    notice = front_joined[2:intro_idx] if intro_idx > 2 else []

    if intro_paras:
        # 简介区每行通常就是一条独立信息（书名/作者/一句话简介），不再做折行合并
        chapters.append({"title": "简介",
                         "paras": [p for p in intro_paras if p.strip()],
                         "vol": -1})

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
    """读取书单；源文件已不存在的条目会被自动下线（书单、章节数据、下载副本一并清掉）。"""
    if not os.path.exists(CONFIG):
        raise BookError("找不到书单配置：%s" % CONFIG)
    with open(CONFIG, encoding="utf-8-sig") as f:
        cfg = json.load(f)
    if not isinstance(cfg, list):
        raise BookError("books/books.json 需要是一个数组，例如 [ { \"id\": \"...\", ... } ]")

    seen, kept = set(), []
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
            print("－ 下线《%s》：原文已不存在（%s）" % (b["title"], b["source"]))
            drop_book_data(b)
            continue
        kept.append(b)

    if len(kept) != len(cfg):
        save_config(kept)
    return kept


def save_config(cfg):
    with open(CONFIG, "w", encoding="utf-8", newline="\n") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
        f.write("\n")


def drop_book_data(b):
    """删掉这本书的章节数据目录与自动生成的下载副本。"""
    import shutil
    out_rel = b.get("dir") or os.path.join("data", b["id"], "c")
    data_dir = os.path.join(ROOT, "data", b["id"])
    if os.path.isdir(data_dir) and os.path.abspath(data_dir) != os.path.abspath(os.path.join(ROOT, "data")):
        shutil.rmtree(data_dir, ignore_errors=True)
    if out_rel and os.path.isdir(resolve(out_rel)):
        shutil.rmtree(resolve(out_rel), ignore_errors=True)
    dl = b.get("download") or ""
    if dl and dl != b.get("source"):
        p = resolve(dl)
        if os.path.isfile(p) and os.path.abspath(p).startswith(os.path.join(ROOT, "books")):
            os.remove(p)
            print("  已删除下载副本：%s" % dl)


# ---------------------------------------------------------------- 自动上架
BOOK_EXT = (".txt", ".md", ".docx", ".epub")
BOOKS_DIR = os.path.join(ROOT, "books")


def guess_meta(path):
    """从文件名/目录名（和文本开头）猜书名 / 作者。"""
    base = os.path.basename(os.path.normpath(path))
    stem = base if os.path.isdir(path) else os.path.splitext(base)[0]
    title = re.sub(r"【[^】]*】|\[[^\]]*\]", "", stem)      # 去掉【精排】这类标记
    author = ""

    m = re.search(r"[（(]\s*([^()（）]{1,20})\s*[)）]\s*$", title)
    if m:
        author = m.group(1).strip()
        title = title[:m.start()]

    m = re.search(r"\s+-\s+([^-]{1,20})$", title)
    if not author and m:
        author = m.group(1).strip()
        title = title[:m.start()]

    if not author:
        head = ""
        try:
            probe = source_files(path)[0] if os.path.isdir(path) else path
            if probe.lower().endswith((".txt", ".md")):
                head = read_text(probe)[:3000]
            elif probe.lower().endswith(".docx"):
                head = "\n".join(docx_lines(probe)[:30])
        except (BookError, OSError, IndexError):
            head = ""
        if head:
            m = re.search(r"作\s*者\s*[:：]\s*(\S{1,20})", head)
            if m:
                author = m.group(1).strip()

    title = re.sub(r"\s*[（(]?\d{1,3}[)）]?\s*$", "", title)  # 去掉 (1)
    title = title.strip(" -—_·")
    return (title or stem), author


def make_id(name, used):
    stem = os.path.splitext(name)[0]
    slug = re.sub(r"[^a-z0-9]+", "-", stem.lower()).strip("-")
    if len(slug) < 3 or slug in used:
        import hashlib
        slug = "bk-" + hashlib.md5(name.encode("utf-8")).hexdigest()[:6]
    base, n = slug, 2
    while slug in used:
        slug = "%s-%d" % (base, n)
        n += 1
    return slug


def auto_register(cfg):
    """把 books/ 里还没登记的「文件」或「目录」自动补进书单（文件名 → 书名/作者）。

    目录 = 一本书，里面的 txt/md/docx 按文件名自然序拼接，适合按章连载。
    """
    used_src = {os.path.abspath(resolve(b["source"])) for b in cfg}
    used_dl = {os.path.abspath(resolve(b.get("download") or b["source"])) for b in cfg}
    used_ids = {b["id"] for b in cfg}
    added = 0

    if not os.path.isdir(BOOKS_DIR):
        return cfg

    for name in sorted(os.listdir(BOOKS_DIR)):
        if name.startswith(".") or name.startswith("~$"):
            continue
        p = os.path.join(BOOKS_DIR, name)
        absp = os.path.abspath(p)

        if os.path.isdir(p):
            inner = [n for n in os.listdir(p)
                     if not n.startswith((".", "~$")) and n.lower().endswith(BOOK_EXT)]
            if not inner:
                continue
            rel_src = "books/" + name
            rel_dl = "books/" + name + ".txt"
            kind = "目录《%s》（%d 个文件）" % (name, len(inner))
        elif os.path.isfile(p) and name.lower().endswith(BOOK_EXT):
            rel_src = "books/" + name
            rel_dl = rel_src if name.lower().endswith((".txt", ".md")) else \
                "books/" + os.path.splitext(name)[0] + ".txt"
            kind = "文件 books/" + name
        else:
            continue

        if absp in used_src or absp in used_dl:
            continue

        title, author = guess_meta(p)
        bid = make_id(name, used_ids)
        cfg.append({
            "id": bid,
            "title": title,
            "author": author,
            "tags": ["自动上架"],
            "note": "自动上架 · " + time.strftime("%Y-%m-%d"),
            "source": rel_src,
            "download": rel_dl,
        })
        used_ids.add(bid)
        used_src.add(absp)
        used_dl.add(os.path.abspath(resolve(rel_dl)))
        added += 1
        print("＋ 自动登记《%s》%s ← %s" % (title, ("／" + author) if author else "", kind))

    if added:
        save_config(cfg)
        print("已把 %d 本新书写回 books/books.json（书名/作者可手改）" % added)
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
    gen_text = os.path.isdir(src) or src.lower().endswith((".epub", ".docx"))
    if os.path.abspath(dl_path) != os.path.abspath(src):
        os.makedirs(os.path.dirname(dl_path) or ROOT, exist_ok=True)
        if gen_text:
            # epub / docx / 目录 都统一导出成纯文本，便于“下载 TXT”
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
    only = set(a for a in sys.argv[1:] if not a.startswith("-"))
    try:
        cfg = load_config()
        cfg = auto_register(cfg)          # books/ 里新丢进来的文件自动补登记
    except BookError as e:
        print("配置错误：", e)
        sys.exit(1)

    if not cfg:
        print("books/ 里还没有可上架的文件（支持 .txt / .md / .docx / .epub）")
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
    cat_path = os.path.join(ROOT, "data", "catalog.js")
    old_cat = ""
    if os.path.exists(cat_path):
        with open(cat_path, encoding="utf-8") as f:
            old_cat = f.read()

    body = "/* 由 tools/build_data.py 自动生成，请勿手改 */\n" \
           "window.MD_PUBLIC_BOOKS = " + json.dumps(catalog, ensure_ascii=False, separators=(",", ":")) + ";\n"
    with open(cat_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(body)

    if body != old_cat:
        bump_version()
    else:
        print("· 目录内容没有变化，版本号保持不变")

    print("\n共 %d 本公开书，总 %d 字，耗时 %.1fs" % (
        len(catalog), sum(b["words"] for b in catalog), time.time() - t0))
    print("下一步：git add -A && git commit -m \"新增书籍\" && git push（Pages 会自动重建，约 1 分钟）")


def bump_version():
    """目录内容变了就让静态资源换 URL，避免浏览器继续用旧 JS / 旧目录。"""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    idx = os.path.join(ROOT, "index.html")
    app = os.path.join(ROOT, "assets", "app.js")
    try:
        html = open(idx, encoding="utf-8").read()
        html = re.sub(r"\?v=[\w.\-]+", "?v=" + stamp, html)
        html = re.sub(r"window\.MD_BUILD = '[^']*'", "window.MD_BUILD = '%s'" % stamp, html)
        open(idx, "w", encoding="utf-8", newline="\n").write(html)

        js = open(app, encoding="utf-8").read()
        js = re.sub(r"const BUILD = '[^']*'", "const BUILD = '%s'" % stamp, js, count=1)
        open(app, "w", encoding="utf-8", newline="\n").write(js)
        print("· 已更新版本号 → %s" % stamp)
    except OSError as e:
        print("· 版本号更新失败（忽略）：%s" % e)


if __name__ == "__main__":
    main()
