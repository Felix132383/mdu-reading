# 墨读 · mdu-reading

一个**纯静态**的中文小说阅读站：公开书架可直接在线阅读，本地 TXT / EPUB 可以导入书架，也可以自己写书并导出。没有后端、没有账号、没有数据库——所有个人数据只留在访问者自己的浏览器里。

线上地址：<https://felix132383.github.io/mdu-reading/>

## 公开书架

| 书名 | 作者 | 章节 | 字数 | 来源 |
|---|---|---|---|---|
| [蛊真人](https://felix132383.github.io/mdu-reading/#/b/gu-zhen-ren/1) | 蛊真人 | 2336 | 730 万 | TXT（校对 5.0） |
| [剑来](https://felix132383.github.io/mdu-reading/#/b/jian-lai/1) | 烽火戏诸侯 | 1144 | 1245 万 | TXT |
| [斗破苍穹](https://felix132383.github.io/mdu-reading/#/b/dou-po-cang-qiong/1) | 天蚕土豆 | 1210 | 513 万 | TXT |
| [神秘复苏](https://felix132383.github.io/mdu-reading/#/b/shen-mi-fu-su/1) | 佛前献花 | 1611 | 531 万 | EPUB（精排） |
| [道诡异仙](https://felix132383.github.io/mdu-reading/#/b/dao-gui-yi-xian/1) | 狐尾的笔 | 1030 | 223 万 | TXT |
| [龙族](https://felix132383.github.io/mdu-reading/#/b/long-zu/1) | 江南 | 124 | 219 万 | EPUB（全套七册） |

## 功能

- **公开书架**：书籍元数据与正文按章节拆分，进站即可读，所有人可见。
- **阅读器**：目录（支持章节搜索、分卷）、上一章/下一章、阅读进度记忆（章节 + 段落位置）、进度条、键盘（←/→ 翻章、空格翻页、`t` 目录、`+/-` 字号）与移动端左右滑动翻章。
- **阅读设置**：宣纸 / 夜读 / 素白三种配色，字号、行距、字体（宋体/黑体）、版心宽度可调，配置保存在 localStorage。
- **本地书**：导入 `.txt` / `.md` / `.epub`（自动识别 UTF-8 与 GB18030 编码、自动切分章节），存入 IndexedDB，不上传任何服务器。
- **写作**：新建书籍、多章节编辑、自动保存、导出 TXT / 导出可离线阅读的单文件 HTML。
- **可分享链接**：`#/b/<书籍 id>/<章节序号>` 可直接定位到某一章。
- 响应式布局、深色主题、打印样式、无障碍标签。

## 目录结构

```
index.html            页面骨架
assets/style.css      样式（含三套主题与响应式）
assets/app.js         应用逻辑（书架 / 阅读器 / 写作 / 导入导出）
books/books.json      ★ 公开书单配置（加书只改这里）
books/*.txt / *.epub  各书原文（随仓库一起发布，供“下载 TXT”）
data/catalog.js       公开书架目录（书名、简介、章节索引），由脚本生成
data/<id>/c/0001.js…  分章正文，每章一个脚本文件，按需加载
data/gu/gu-zhen-ren.txt  《蛊真人》原文（历史位置）
tools/build_data.py   把 TXT / EPUB 转换成上面的数据
```

> 构建脚本支持两种源文件：`.txt`（自动识别 UTF-8 / GB18030，自动过滤盗版站点水印、识别「正文 第X章」这类前缀）和 `.epub`（按 NCX 目录切章，父级目录自动变成“卷”）。

## 加一本新书到公开书架

**第 1 步：把小说 txt 放进 `books/` 目录**（文件名随意，建议和书同名）

章节标题单独占一行即可被识别，这些写法都支持：

```
第一章 开端         第12章 xxx        第十二回 xxx
第二卷              第一节 xxx        番外篇 某篇
序章 / 序言 / 楔子 / 引子 / 前言 / 尾声 / 终章 / 后记 / Chapter 1
```

编码 UTF-8 或 GBK/GB18030 都行（脚本会自动识别）。标题行建议不超过 36 字、不以句号结尾，以免被当成正文。

**第 2 步：在 `books/books.json` 里加一条记录**

```json
{
  "id": "dou-po-cang-qiong",
  "title": "斗破苍穹",
  "author": "天蚕土豆",
  "tags": ["玄幻", "完本"],
  "note": "精校版",
  "source": "books/斗破苍穹.txt",
  "download": "books/斗破苍穹.txt"
}
```

| 字段 | 说明 |
|---|---|
| `id` | 必填，小写字母/数字/连字符，决定链接 `#/b/<id>/<章>`，不要和已有书重复 |
| `title` / `author` / `tags` / `note` | 书架卡片上显示的内容 |
| `source` | 必填，原文路径（相对仓库根目录，也可写绝对路径） |
| `download` | 可选，"下载 TXT" 指向的文件；和 `source` 相同则不复制，不同则自动复制一份 |
| `dir` | 可选，章节输出目录，默认 `data/<id>/c`（一般不用写） |

**第 3 步：重建数据**

```bash
python tools/build_data.py
```

终端会打印每本书的章节数/字数，并重写 `data/catalog.js` 与 `data/<id>/c/*.js`。
重复运行是幂等的（内容没变时 git 不会产生改动），旧的多余章节文件会自动清理。

**第 4 步：提交并推送**

```bash
git add -A
git commit -m "新增书籍：斗破苍穹"
git push
```

推送后 GitHub Pages 会自动重新构建（约 1 分钟）。刷新页面若没看到新书，按 `Ctrl+F5` 强刷一下（`catalog.js` 被浏览器缓存了）。

> 删除一本书：从 `books.json` 里删掉那条记录 → 删掉 `data/<id>/` 目录 → 重新运行脚本 → 提交推送。

## 本地预览

```bash
python -m http.server 8787
# 打开 http://127.0.0.1:8787/
```

> 直接双击 `index.html` 也可以打开（数据用 `<script>` 加载，不依赖 fetch），但用 HTTP 服务更接近线上环境。

## 部署

站点是纯静态文件，推到 GitHub 仓库后开启 **Settings → Pages**（分支 `main`，目录 `/`）即可。
仓库里的 `.nojekyll` 用于关闭 GitHub Pages 的 Jekyll 处理，保证文件名与目录原样发布。

### 发布时记得改版本号（避免浏览器用旧脚本）

GitHub Pages 给静态资源的缓存是 `max-age=600`，如果只改 `app.js` 而 URL 不变，访问者可能十几分钟内仍在跑旧代码。所以每次发布要同步改三处同名字符串（例如 `20261031-2` → `20261101-1`）：

1. `index.html` 里 `<link href="assets/style.css?v=…">`
2. `index.html` 里 `window.MD_BUILD = '…'` 与两个 `<script src="…?v=…">`
3. `assets/app.js` 顶部的 `const BUILD = '…'`

`window.MD_BUILD` 与 `BUILD` 不一致时，页面底部会自动弹出「本站已更新，请刷新」的提示条，用户不会再默默读到旧逻辑。

## 版权

书架中的书籍内容版权归原作者及出版方所有，本项目仅用于个人学习与技术交流，请勿用于商业用途。
