# -*- coding: utf-8 -*-
"""一条命令上架：把 txt / md / docx / epub 丢进 books/，重建数据并推送。

用法：
    python tools/publish.py "D:\\下载\\某某小说.txt"
    python tools/publish.py a.txt b.epub --no-push          # 只构建，不推送
    python tools/publish.py c.docx --title 书名 --author 作者
"""
import os
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BOOKS = os.path.join(ROOT, "books")
SUPPORTED = (".txt", ".md", ".docx", ".epub")


def die(msg):
    print("✗ " + msg)
    sys.exit(1)


def copy_in(src):
    """把源文件（或整个目录）复制进 books/。

    目录 = 一本书，里面放 0001.txt、0002.txt… 就是按章连载。
    """
    if os.path.isdir(src):
        name = os.path.basename(os.path.normpath(src))
        dst = os.path.join(BOOKS, name)
        os.makedirs(dst, exist_ok=True)
        n = 0
        for root, _, files in os.walk(src):
            for f in files:
                if f.startswith((".", "~$")):
                    continue
                s = os.path.join(root, f)
                if os.path.splitext(f)[1].lower() not in SUPPORTED:
                    continue
                d = os.path.join(dst, os.path.relpath(s, src))
                os.makedirs(os.path.dirname(d), exist_ok=True)
                shutil.copy2(s, d)
                n += 1
        if not n:
            die("目录里没有可用的 txt/md/docx：%s" % src)
        print("→ 已放入 books/%s/（%d 个文件）" % (name, n))
        return dst

    if not os.path.isfile(src):
        die("文件或目录不存在：%s" % src)
    ext = os.path.splitext(src)[1].lower()
    if ext not in SUPPORTED:
        die("不支持的格式：%s（只支持 %s）" % (ext, " / ".join(SUPPORTED)))
    os.makedirs(BOOKS, exist_ok=True)
    base = os.path.splitext(os.path.basename(src))[0]
    name = os.path.basename(src)
    n = 2
    while os.path.exists(os.path.join(BOOKS, name)):
        if os.path.getsize(os.path.join(BOOKS, name)) == os.path.getsize(src):
            print("· 已存在同名同大小文件，跳过复制：%s" % name)
            return os.path.join(BOOKS, name)
        name = "%s-%d%s" % (base, n, ext)
        n += 1
    shutil.copy2(src, os.path.join(BOOKS, name))
    print("→ 已放入 books/%s（%.1f MB）" % (name, os.path.getsize(src) / 1048576))
    return os.path.join(BOOKS, name)


def run(cmd, check=True):
    env = dict(os.environ, PYTHONUNBUFFERED="1")
    p = subprocess.run(cmd, cwd=ROOT, env=env)
    if check and p.returncode != 0:
        die("命令失败：%s" % " ".join(cmd))
    return p.returncode


def main():
    args = [a for a in sys.argv[1:]]
    push = "--no-push" not in args
    overrides = {}
    plain = []
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("--title", "--author") and i + 1 < len(args):
            overrides[a[2:]] = args[i + 1]
            i += 2
            continue
        if a.startswith("--"):
            i += 1
            continue
        plain.append(a)
        i += 1

    if not plain:
        print(__doc__)
        sys.exit(1)

    t0 = time.time()
    copied = []
    for f in plain:
        copied.append(copy_in(os.path.abspath(f)))

    print("\n[1/2] 解析并生成章节数据 …")
    run([sys.executable, os.path.join(ROOT, "tools", "build_data.py")])

    if overrides:
        # 自动登记已经完成，按 source 找到刚加的条目再改书名/作者，然后重建一次
        import json
        cfg_path = os.path.join(BOOKS, "books.json")
        cfg = json.load(open(cfg_path, encoding="utf-8-sig"))
        rels = {os.path.relpath(p, ROOT).replace("\\", "/") for p in copied}
        touched = False
        for b in cfg:
            if b.get("source") in rels:
                if overrides.get("title"):
                    b["title"] = overrides["title"]
                if overrides.get("author"):
                    b["author"] = overrides["author"]
                touched = True
        if touched:
            with open(cfg_path, "w", encoding="utf-8", newline="\n") as fh:
                json.dump(cfg, fh, ensure_ascii=False, indent=2)
                fh.write("\n")
            print("· 已应用自定义书名/作者，重新生成目录 …")
            run([sys.executable, os.path.join(ROOT, "tools", "build_data.py")])

    if not push:
        print("\n完成（未推送）。检查无误后执行：git add -A && git commit -m \"上架\" && git push")
        return

    print("\n[2/2] 提交并推送 …")
    run(["git", "add", "-A"])
    status = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=ROOT)
    if status.returncode == 0:
        print("没有需要提交的改动。")
        return
    run(["git", "commit", "-m", "上架新书：" + "、".join(os.path.splitext(os.path.basename(f))[0] for f in plain)])
    if run(["git", "push"], check=False) != 0:
        die("推送失败：请确认本机 git 已登录 GitHub，或手动执行 git push")
    print("\n✅ 已推送，GitHub Pages 约 1 分钟后生效：")
    print("   https://felix132383.github.io/mdu-reading/")
    print("   （用时 %.1fs）" % (time.time() - t0))


if __name__ == "__main__":
    main()
