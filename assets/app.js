/* ==========================================================================
   墨读 · 应用逻辑
   - 纯静态，无需后端；公开书架数据由 data/catalog.js + data/gu/c/*.js 提供
   - 本地书（导入 / 原创）存在 IndexedDB
   ========================================================================== */
(function () {
  'use strict';

  /* ---------------------------------------------------------------- 基础工具 */
  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.prototype.slice.call((root || document).querySelectorAll(sel));
  const ESC_MAP = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ESC_MAP[c]);
  const uid = () => 'b' + Date.now().toString(36) + Math.random().toString(36).slice(2, 7);
  const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
  const words = s => (s || '').replace(/\s+/g, '').length;
  const fmt = n => {
    n = n || 0;
    if (n >= 100000000) return (n / 100000000).toFixed(1).replace(/\.0$/, '') + '亿';
    if (n >= 10000) return (n / 10000).toFixed(n >= 1000000 ? 0 : 1).replace(/\.0$/, '') + '万';
    return String(n);
  };
  const debounce = (fn, ms) => { let t; return function () { const a = arguments; clearTimeout(t); t = setTimeout(() => fn.apply(null, a), ms); }; };
  const raf2 = () => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));

  const store = {
    get(k, d) { try { const v = localStorage.getItem(k); return v == null ? d : JSON.parse(v); } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* 隐私模式忽略 */ } }
  };

  let toastTimer = null;
  function toast(msg) {
    const el = $('#toast');
    el.textContent = msg;
    el.classList.add('on');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.remove('on'), 1900);
  }

  /* ---------------------------------------------------------------- 阅读设置 */
  const DEFAULTS = { theme: 'sepia', fs: 19, lh: 1.9, font: 'serif', width: 780 };
  const settings = Object.assign({}, DEFAULTS, store.get('modu-settings', {}));

  function applySettings() {
    document.body.dataset.theme = settings.theme;
    const root = document.documentElement.style;
    root.setProperty('--fs', settings.fs + 'px');
    root.setProperty('--lh', String(settings.lh));
    root.setProperty('--wrap', settings.width + 'px');
    root.setProperty('--font-read', settings.font === 'sans'
      ? 'var(--font-ui)'
      : 'var(--font-serif)');
    store.set('modu-settings', settings);
    const meta = $('meta[name="theme-color"]');
    if (meta) meta.setAttribute('content', getComputedStyle(document.body).getPropertyValue('--bg').trim() || '#f3ede2');
    syncSettingUI();
  }

  function syncSettingUI() {
    const fs = $('#setFs'), lh = $('#setLh');
    if (!fs) return;
    fs.value = settings.fs;
    lh.value = settings.lh;
    $('#setFsVal').textContent = settings.fs + 'px';
    $('#setLhVal').textContent = Number(settings.lh).toFixed(1);
    $$('#segFont button').forEach(b => b.classList.toggle('on', b.dataset.v === settings.font));
    $$('#segWidth button').forEach(b => b.classList.toggle('on', +b.dataset.v === settings.width));
  }

  function cycleTheme() {
    const order = ['sepia', 'dark', 'light'];
    settings.theme = order[(order.indexOf(settings.theme) + 1) % order.length];
    applySettings();
    toast({ sepia: '宣纸', dark: '夜读', light: '素白' }[settings.theme]);
  }

  /* ------------------------------------------------------------ IndexedDB */
  const DB = (() => {
    let db = null;
    return {
      open() {
        return new Promise((resolve, reject) => {
          if (!('indexedDB' in window)) return reject(new Error('浏览器不支持 IndexedDB'));
          const req = indexedDB.open('modu-db', 1);
          req.onupgradeneeded = e => {
            const d = e.target.result;
            if (!d.objectStoreNames.contains('books')) d.createObjectStore('books', { keyPath: 'id' });
          };
          req.onsuccess = e => { db = e.target.result; resolve(); };
          req.onerror = () => reject(req.error);
        });
      },
      _wrap(fn) {
        return new Promise((resolve, reject) => {
          const tx = db.transaction('books', fn.mode);
          const req = fn.run(tx.objectStore('books'));
          req.onsuccess = () => resolve(req.result);
          req.onerror = () => reject(req.error);
        });
      },
      all() { return this._wrap({ mode: 'readonly', run: s => s.getAll() }); },
      get(id) { return this._wrap({ mode: 'readonly', run: s => s.get(id) }); },
      put(b) { return this._wrap({ mode: 'readwrite', run: s => s.put(b) }); },
      del(id) { return this._wrap({ mode: 'readwrite', run: s => s.delete(id) }); }
    };
  })();

  /* --------------------------------------------------------- 阅读进度存储 */
  const progAll = store.get('modu-progress', {});
  function progressOf(id) { return progAll[id] || null; }
  const saveProg = debounce(() => store.set('modu-progress', progAll), 400);
  function setProgress(id, ci, ratio) {
    progAll[id] = { ci: ci, ratio: clamp(ratio || 0, 0, 1), at: Date.now() };
    store.set('modu-last', { id: id, ci: ci, at: Date.now() });
    saveProg();
  }
  function bookPercent(book) {
    const p = progressOf(book.id);
    if (!p) return 0;
    const n = Math.max(1, book.chapters.length);
    return Math.round(clamp((p.ci + (p.ratio || 0)) / n, 0, 1) * 100);
  }

  /* ------------------------------------------------------------ 章节数据加载 */
  const chapterCache = new Map();      // fid -> string[]
  const pending = new Map();           // fid -> {resolve, reject}

  window.__MDC = function (fid, paras) {
    chapterCache.set(fid, paras);
    const p = pending.get(fid);
    if (p) { pending.delete(fid); p.resolve(paras); }
  };

  function loadChapterFile(url, fid) {
    if (chapterCache.has(fid)) return Promise.resolve(chapterCache.get(fid));
    return new Promise((resolve, reject) => {
      let done = false;
      const s = document.createElement('script');
      s.src = url;
      s.async = true;
      pending.set(fid, {
        resolve(p) { done = true; resolve(p); },
        reject(e) { done = true; reject(e); }
      });
      s.onload = () => {
        s.remove();
        if (!done) {
          pending.delete(fid);
          chapterCache.has(fid) ? resolve(chapterCache.get(fid)) : reject(new Error('章节数据为空'));
        }
      };
      s.onerror = () => {
        s.remove();
        pending.delete(fid);
        if (!done) reject(new Error('章节文件加载失败'));
      };
      document.head.appendChild(s);
    });
  }

  function getChapterParas(book, i) {
    const ch = book.chapters[i];
    if (!ch) return Promise.resolve([]);
    if (ch.content != null) {
      if (Array.isArray(ch.content)) return Promise.resolve(ch.content);
      return Promise.resolve(ch.content.split('\n').map(s => s.trim()).filter(Boolean));
    }
    return loadChapterFile((book.dir || 'data/gu/c/') + ch.f + '.js', ch.f);
  }

  /* -------------------------------------------------------------- 书架数据 */
  const state = {
    publicBooks: [],
    localBooks: [],
    keyword: '',
    reader: { book: null, idx: 0 },
    writer: null
  };

  function normalizePublic(b) {
    return Object.assign({}, b, {
      source: 'public',
      title: b.title || '未命名',
      author: b.author || '',
      tags: b.tags || [],
      chapters: (b.chapters || []).map(c => ({ t: c.t, f: c.f, w: c.w || 0, v: c.v }))
    });
  }

  function findBook(id) {
    return state.publicBooks.find(b => b.id === id) || state.localBooks.find(b => b.id === id) || null;
  }

  async function loadLocalBooks() {
    try {
      const rows = await DB.all();
      state.localBooks = rows.map(r => {
        const chapters = (r.chapters || []).map(c => ({
          id: c.id || uid(),
          t: c.title || '未命名章节',
          content: c.content || ''
        }));
        return {
          id: r.id, title: r.title || '未命名作品', author: r.author || '',
          source: 'local', kind: r.kind || 'read',
          tags: r.kind === 'write' ? ['原创'] : ['本地导入'],
          chapters: chapters,
          words: r.words || chapters.reduce((s, c) => s + words(c.content), 0),
          createdAt: r.createdAt || 0,
          updatedAt: r.updatedAt || r.createdAt || 0
        };
      }).sort((a, b) => (b.updatedAt || 0) - (a.updatedAt || 0));
      state.localBooks.forEach(b => { if (!b.words) b.words = b.chapters.reduce((s, c) => s + words(c.content), 0); });
    } catch (e) {
      console.warn('本地书架读取失败', e);
      state.localBooks = [];
    }
  }

  async function persistLocal(book) {
    const chapters = book.chapters.map(c => ({
      id: c.id || uid(),
      title: c.t || c.title || '未命名章节',
      content: c.content == null ? '' : (Array.isArray(c.content) ? c.content.join('\n') : c.content)
    }));
    await DB.put({
      id: book.id, title: book.title, author: book.author || '',
      chapters: chapters, kind: book.kind || 'read',
      words: book.words || chapters.reduce((s, c) => s + words(c.content), 0),
      createdAt: book.createdAt || Date.now(), updatedAt: Date.now()
    });
  }

  /* -------------------------------------------------------------- 书架渲染 */
  function coverTitle(title) {
    const t = String(title || '书').replace(/\s+/g, '');
    return t.length > 8 ? t.slice(0, 8) : t;
  }

  // 封面竖排书名：字多就缩小，避免被裁切
  function coverStyle(text) {
    const n = text.length;
    const fs = n >= 7 ? 11 : n >= 6 ? 12 : n >= 5 ? 13 : n >= 4 ? 15 : 17;
    const ls = n >= 5 ? '.1em' : '.2em';
    return `font-size:${fs}px;letter-spacing:${ls}`;
  }

  function cardHTML(b) {
    const p = progressOf(b.id);
    const pct = bookPercent(b);
    const isPub = b.source === 'public';
    const ct = coverTitle(b.title);
    return `<article class="card" data-id="${esc(b.id)}" data-act="open" tabindex="0" role="button" aria-label="${esc(b.title)}">
      <div class="cover" aria-hidden="true"><span style="${coverStyle(ct)}">${esc(ct)}</span></div>
      <div class="card-body">
        <h3>${esc(b.title)}</h3>
        <div class="byline">${esc(b.author || '佚名')}</div>
        ${b.note ? `<div class="note" title="${esc(b.note)}">${esc(b.note)}</div>` : ''}
        <div class="tags">
          ${(b.tags || []).map(t => `<span class="tag">${esc(t)}</span>`).join('')}
          <span class="tag plain">${isPub ? '公开' : '本机'}</span>
        </div>
        <div class="meta">${b.chapters.length} 章 · ${fmt(b.words)}字</div>
        <div class="progress"><i style="width:${pct}%"></i></div>
        <div class="meta">${p ? `读至第 ${p.ci + 1} / ${b.chapters.length} 章 · ${pct}%` : '尚未开始'}</div>
        <div class="acts">
          <button class="btn primary" data-act="read">${p ? '继续阅读' : '开始阅读'}</button>
          ${isPub && b.txt ? `<a class="btn" href="${esc(b.txt)}" download>下载 TXT</a>` : ''}
        </div>
      </div>
    </article>`;
  }

  function rowHTML(b) {
    const p = progressOf(b.id);
    const pct = bookPercent(b);
    return `<div class="row" data-id="${esc(b.id)}">
      <div class="grow">
        <h4>${esc(b.title)}</h4>
        <div class="meta">${b.chapters.length} 章 · ${fmt(b.words)}字 · ${p ? `已读 ${pct}%` : '未读'}</div>
      </div>
      <div class="acts">
        <button class="btn" data-act="read">${p ? '继续' : '阅读'}</button>
        <button class="btn" data-act="write">编辑</button>
        <button class="btn" data-act="del" aria-label="删除">删除</button>
      </div>
    </div>`;
  }

  function renderShelf() {
    const kw = state.keyword.trim().toLowerCase();
    const match = b => !kw || b.title.toLowerCase().includes(kw) || (b.author || '').toLowerCase().includes(kw);

    const pubs = state.publicBooks.filter(match);
    const locs = state.localBooks.filter(match);

    $('#shelfPublic').innerHTML = pubs.length
      ? pubs.map(cardHTML).join('')
      : `<div class="empty" style="grid-column:1/-1">${kw ? '没有匹配的公开书籍' : '暂无公开书籍'}</div>`;
    $('#pubCount').textContent = pubs.length ? `${pubs.length} 本` : '';

    $('#shelfLocal').innerHTML = locs.length
      ? locs.map(rowHTML).join('')
      : `<div class="empty">${kw ? '本地书架没有匹配的书籍'
        : '<b>书架还是空的</b><br>点击右上角「导入文件」读 TXT / EPUB，或「写新书」开始创作'}</div>`;
    $('#locCount').textContent = locs.length ? `${locs.length} 本` : '';

    const totalWords = state.publicBooks.reduce((s, b) => s + (b.words || 0), 0);
    $('#footStats').textContent = `公开 ${state.publicBooks.length} 本 · ${fmt(totalWords)}字`;
    $('#footDate').textContent = '更新于 ' + new Date(document.lastModified || Date.now()).toLocaleDateString('zh-CN');

    // 继续阅读按钮
    const last = store.get('modu-last', null);
    const lastBook = last ? findBook(last.id) : null;
    const btn = $('#btnResume');
    if (lastBook) {
      btn.textContent = '继续阅读《' + lastBook.title + '》';
      btn.disabled = false;
      btn.dataset.id = lastBook.id;
    } else if (state.publicBooks[0]) {
      btn.textContent = '开始阅读《' + state.publicBooks[0].title + '》';
      btn.disabled = false;
      btn.dataset.id = state.publicBooks[0].id;
    } else {
      btn.textContent = '导入一本书开始';
      btn.disabled = true;
      delete btn.dataset.id;
    }
  }

  /* -------------------------------------------------------------- 视图切换 */
  function showView(name) {
    document.body.dataset.view = name;
    $$('.view').forEach(v => v.classList.toggle('active', v.id === 'view-' + name));
    $$('.shelf-only').forEach(el => { el.style.display = name === 'shelf' ? '' : 'none'; });
    if (name !== 'reader') $('#readbar').classList.remove('on');
    window.scrollTo(0, 0);
  }

  /* ------------------------------------------------------------------ 路由 */
  function setHash(hash, replace) {
    try {
      if (replace) history.replaceState(null, '', hash);
      else if (location.hash === hash) route(true);
      else location.hash = hash;
    } catch (e) {
      location.hash = hash;
    }
  }

  function parseRoute() {
    const h = String(location.hash || '').replace(/^#/, '');
    let m = h.match(/^\/b\/([^/]+)(?:\/(\d+))?\/?$/);
    if (m) return { view: 'reader', id: decodeURIComponent(m[1]), chapter: m[2] ? +m[2] - 1 : null };
    m = h.match(/^\/w\/([^/]+)\/?$/);
    if (m) return { view: 'writer', id: decodeURIComponent(m[1]) };
    return { view: 'shelf' };
  }

  let routing = false;
  async function route(force) {
    if (routing) return;
    routing = true;
    try {
      const r = parseRoute();
      if (r.view === 'reader') {
        if (!force && state.reader.book && state.reader.book.id === r.id) {
          if (r.chapter == null || r.chapter === state.reader.idx) { showView('reader'); return; }
          await showChapter(r.chapter);
          return;
        }
        await openReader(r.id, r.chapter);
      } else if (r.view === 'writer') {
        const b = findBook(r.id);
        if (b) openWriter(b); else { toast('找不到这本书'); setHash('#/', true); }
      } else {
        showView('shelf');
        renderShelf();
      }
    } finally {
      routing = false;
    }
  }

  /* ------------------------------------------------------------------ 阅读器 */
  async function openReader(id, chapterIdx) {
    const book = findBook(id);
    if (!book) { toast('找不到这本书'); setHash('#/', true); return; }
    closeToc();
    state.reader.book = book;
    document.body.dataset.view = 'reader';
    showView('reader');
    $('#rBookTitle').textContent = book.title;
    renderToc(book);

    const p = progressOf(book.id);
    let idx = chapterIdx != null ? chapterIdx : (p ? p.ci : 0);
    idx = clamp(idx, 0, book.chapters.length - 1);
    const ratio = (chapterIdx == null && p && p.ci === idx) ? (p.ratio || 0) : 0;
    await showChapter(idx, ratio);
    setHash('#/b/' + encodeURIComponent(book.id) + '/' + (idx + 1), true);
  }

  function renderToc(book) {
    const list = $('#tocList');
    const kw = ($('#tocSearch').value || '').trim().toLowerCase();
    const vols = book.volumes || [];
    const volAt = new Map();
    vols.forEach(v => volAt.set(v.start, v.title));

    const parts = [];
    book.chapters.forEach((c, i) => {
      if (kw && !c.t.toLowerCase().includes(kw)) return;
      if (volAt.has(i) && !kw) parts.push(`<div class="toc-vol">${esc(volAt.get(i))}</div>`);
      parts.push(`<div class="toc-item" data-i="${i}" role="option" aria-selected="false">
        <span class="n">${esc(c.t)}</span><span class="c">${c.w ? fmt(c.w) : ''}</span></div>`);
    });
    list.innerHTML = parts.length ? parts.join('') : '<div class="toc-empty">没有匹配的章节</div>';
    list.scrollTop = 0;
    markToc(state.reader.idx);
  }

  function markToc(i) {
    const list = $('#tocList');
    let active = null;
    $$('.toc-item', list).forEach(el => {
      const on = +el.dataset.i === i;
      el.classList.toggle('active', on);
      el.setAttribute('aria-selected', on ? 'true' : 'false');
      if (on) active = el;
    });
    if (active && !list.dataset.noScroll) {
      const r = active.getBoundingClientRect();
      const lr = list.getBoundingClientRect();
      if (r.top < lr.top + 30 || r.bottom > lr.bottom - 30) {
        list.scrollTop += r.top - lr.top - lr.height / 2 + r.height / 2;
      }
    }
  }

  let chapterToken = 0;
  async function showChapter(i, ratio) {
    const book = state.reader.book;
    if (!book) return;
    i = clamp(i, 0, book.chapters.length - 1);
    state.reader.idx = i;
    const ch = book.chapters[i];
    const token = ++chapterToken;

    document.body.dataset.view = 'reader';
    $('#article').innerHTML = '<div class="skeleton">' + '<i></i>'.repeat(9) + '</div>';
    $('#rPos').textContent = `${i + 1} / ${book.chapters.length}`;
    updateNav();
    $('#readbar').classList.add('on');
    document.title = `${ch.t} · ${book.title} · 墨读`;

    let paras;
    try {
      paras = await getChapterParas(book, i);
    } catch (e) {
      if (token !== chapterToken) return;
      $('#article').innerHTML = `<div class="load-err">本章加载失败：${esc(e.message)}<br>
        请检查网络或章节数据后 <button class="btn" id="btnRetry">重试</button></div>`;
      const retry = $('#btnRetry');
      if (retry) retry.onclick = () => showChapter(i);
      return;
    }
    if (token !== chapterToken) return;

    $('#article').innerHTML = `<h1>${esc(ch.t)}</h1>` + paras.map(p => `<p>${esc(p)}</p>`).join('');
    markToc(i);
    setHash('#/b/' + encodeURIComponent(book.id) + '/' + (i + 1), true);

    await raf2();
    if (ratio > 0) {
      const max = document.documentElement.scrollHeight - window.innerHeight;
      window.scrollTo(0, clamp(ratio, 0, 1) * Math.max(0, max));
    } else {
      window.scrollTo(0, 0);
    }
    updateReadbar();
    updateNav();

    // 预取下一章
    if (book.source === 'public' && i + 1 < book.chapters.length) {
      const next = book.chapters[i + 1];
      if (!chapterCache.has(next.f)) {
        const job = () => loadChapterFile((book.dir || 'data/gu/c/') + next.f + '.js', next.f).catch(() => {});
        if (window.requestIdleCallback) window.requestIdleCallback(job, { timeout: 1500 });
        else setTimeout(job, 400);
      }
    }
  }

  function updateNav() {
    const book = state.reader.book;
    if (!book) return;
    const i = state.reader.idx;
    const last = book.chapters.length - 1;
    $('#btnPrev').disabled = i <= 0;
    $('#btnPrev2').disabled = i <= 0;
    $('#btnNext').disabled = i >= last;
    $('#btnNext2').textContent = i >= last ? '已是最后一章' : '下一章 →';
    $('#btnNext2').disabled = i >= last;
  }

  function updateReadbar() {
    const book = state.reader.book;
    if (!book || document.body.dataset.view !== 'reader') return;
    const max = document.documentElement.scrollHeight - window.innerHeight;
    const ratio = max > 0 ? clamp(window.scrollY / max, 0, 1) : 0;
    const overall = (state.reader.idx + ratio) / Math.max(1, book.chapters.length);
    $('#readbar').style.width = (overall * 100).toFixed(2) + '%';
  }

  const onScroll = () => {
    updateReadbar();
    saveProgressSoon();
  };

  const saveProgressSoon = debounce(() => {
    const book = state.reader.book;
    if (!book || document.body.dataset.view !== 'reader') return;
    const max = document.documentElement.scrollHeight - window.innerHeight;
    const ratio = max > 0 ? clamp(window.scrollY / max, 0, 1) : 0;
    setProgress(book.id, state.reader.idx, ratio);
  }, 320);

  function gotoChapter(i) {
    const book = state.reader.book;
    if (!book) return;
    if (i < 0) { toast('已经是第一章'); return; }
    if (i >= book.chapters.length) { toast('已经是最后一章'); return; }
    setProgress(book.id, i, 0);
    showChapter(i);
  }

  /* -------------------------------------------------------------- 目录抽屉 */
  const isMobile = () => {
    try { return window.matchMedia('(max-width: 900px)').matches; }
    catch (e) { return window.innerWidth <= 900; }
  };

  function openToc(el) {
    if (!el) return;
    if (isMobile()) {
      el.classList.add('open');
      $('#mask').classList.add('on');
    } else {
      document.body.classList.toggle('toc-hidden');
    }
  }
  function closeToc() {
    $$('.toc.open').forEach(el => el.classList.remove('open'));
    $('#mask').classList.remove('on');
  }

  /* ------------------------------------------------------------------ 写作器 */
  let saveTimer = null;
  const scheduleSave = () => { clearTimeout(saveTimer); saveTimer = setTimeout(forceSave, 500); };

  function openWriter(book) {
    if (book.source === 'public') { toast('公开书籍不可编辑'); return; }
    closeToc();
    state.writer = book;
    state.reader.book = null;
    showView('writer');
    $('#wTitle').value = book.title;
    state.reader.idx = 0;
    renderWriterChapters();
    loadWriterChapter(0);
  }

  function renderWriterChapters() {
    const b = state.writer;
    if (!b) return;
    $('#wChapters').innerHTML = b.chapters.map((c, i) =>
      `<div class="toc-item${i === state.reader.idx ? ' active' : ''}" data-i="${i}" role="option">
         <span class="n">${esc(c.t || '未命名章节')}</span>
         <span class="c">${fmt(words(c.content))}</span>
         <span class="del" title="删除本章" aria-label="删除本章">×</span></div>`).join('');
  }

  function loadWriterChapter(i) {
    const b = state.writer;
    if (!b) return;
    i = clamp(i, 0, b.chapters.length - 1);
    state.reader.idx = i;
    const ch = b.chapters[i];
    $('#chTitle').value = ch.t || '';
    $('#chBody').value = Array.isArray(ch.content) ? ch.content.join('\n') : (ch.content || '');
    renderWriterChapters();
    updateWStat();
  }

  function updateWStat() {
    const b = state.writer;
    if (!b) return;
    const total = b.chapters.reduce((s, c) => s + words(Array.isArray(c.content) ? c.content.join('') : c.content), 0);
    const cur = words($('#chBody').value);
    $('#wStat').textContent = `本章 ${fmt(cur)} 字 · 全书 ${fmt(total)} 字`;
    // 侧栏当前章标题/字数实时同步
    const active = $('#wChapters .toc-item.active');
    if (active) {
      const n = active.querySelector('.n');
      const c = active.querySelector('.c');
      if (n) n.textContent = $('#chTitle').value.trim() || '未命名章节';
      if (c) c.textContent = fmt(cur);
    }
  }

  async function forceSave() {
    clearTimeout(saveTimer);
    const b = state.writer;
    if (!b || document.body.dataset.view !== 'writer') return;
    b.title = $('#wTitle').value.trim() || '未命名作品';
    const ch = b.chapters[state.reader.idx];
    if (ch) {
      ch.t = $('#chTitle').value.trim() || '未命名章节';
      ch.content = $('#chBody').value;
    }
    b.words = b.chapters.reduce((s, c) => s + words(Array.isArray(c.content) ? c.content.join('') : c.content), 0);
    b.updatedAt = Date.now();
    try { await persistLocal(b); } catch (e) { console.warn(e); }
    updateWStat();
  }

  /* ------------------------------------------------------------------ 导入 */
  // 章节标题：以「第X章/节/回…」「序章」「番外」「Chapter N」开头，
  // 且整行较短、不以句末标点结尾（避免把「第二章内容。」这类正文误判成标题）
  const CH_RE = /^\s*(第\s*[0-9零一二三四五六七八九十百千万两]{1,8}\s*[章节回卷集部篇幕话]|序章|序言|楔子|引子|前言|尾声|终章|后记|番外|Chapter\s+\d+)[^\n]{0,30}$/;
  const CH_END_BAD = /[。！？…，,；;、]$/;

  function isChapterHeading(line) {
    const t = line.trim();
    return t.length > 0 && t.length <= 36 && CH_RE.test(t) && !CH_END_BAD.test(t);
  }

  function splitChapters(text) {
    const lines = String(text).replace(/\r\n?/g, '\n').split('\n');
    const chunks = [];
    let cur = { title: '正文', lines: [], heading: false };
    for (const line of lines) {
      if (isChapterHeading(line)) {
        if (cur.lines.join('').trim()) chunks.push(cur);
        cur = { title: line.trim(), lines: [], heading: true };
      } else {
        cur.lines.push(line);
      }
    }
    if (cur.lines.join('').trim()) chunks.push(cur);
    if (!chunks.length || chunks.every(c => !c.heading)) {
      return [{ id: uid(), t: '正文', content: String(text).trim() }];
    }
    return chunks.map(c => ({ id: uid(), t: c.title, content: c.lines.join('\n').trim() }));
  }

  function decodeBuffer(buf) {
    try { return new TextDecoder('utf-8', { fatal: true }).decode(buf); }
    catch (e) {
      try { return new TextDecoder('gb18030').decode(buf); }
      catch (e2) { return new TextDecoder('utf-8').decode(buf); }
    }
  }

  function loadJSZip() {
    if (window.JSZip) return Promise.resolve();
    return new Promise((resolve, reject) => {
      const s = document.createElement('script');
      s.src = 'https://cdn.jsdelivr.net/npm/jszip@3.10.1/dist/jszip.min.js';
      s.onload = resolve;
      s.onerror = () => reject(new Error('无法加载 EPUB 解析库（需要联网）'));
      document.head.appendChild(s);
    });
  }

  async function parseEpub(file) {
    await loadJSZip();
    const zip = await JSZip.loadAsync(file);
    const container = zip.file('META-INF/container.xml');
    if (!container) throw new Error('不是有效的 EPUB 文件');
    const rootPath = (await container.async('string')).match(/full-path="([^"]+)"/)[1];
    const base = rootPath.includes('/') ? rootPath.slice(0, rootPath.lastIndexOf('/') + 1) : '';
    const opf = await zip.file(rootPath).async('string');

    const items = {};
    opf.replace(/<item\b[^>]*>/g, tag => {
      const id = (tag.match(/id="([^"]+)"/) || [])[1];
      const href = (tag.match(/href="([^"]+)"/) || [])[1];
      if (id && href) items[id] = decodeURIComponent(href);
      return tag;
    });
    const spine = Array.from(opf.matchAll(/<itemref[^>]*idref="([^"]+)"/g)).map(m => m[1]);

    const chapters = [];
    for (const id of spine) {
      const href = items[id];
      if (!href) continue;
      const f = zip.file(base + href);
      if (!f) continue;
      const doc = new DOMParser().parseFromString(await f.async('string'), 'text/html');
      doc.querySelectorAll('script,style').forEach(n => n.remove());
      const title = ((doc.querySelector('h1,h2,h3,title') || {}).textContent || '').trim() || `第 ${chapters.length + 1} 节`;
      const content = (doc.body ? doc.body.textContent : '').replace(/[ \t]+/g, ' ').replace(/\n{3,}/g, '\n\n').trim();
      if (content) chapters.push({ id: uid(), t: title, content });
    }
    return chapters;
  }

  async function importFiles(fileList) {
    const files = Array.prototype.slice.call(fileList || []);
    if (!files.length) return;
    for (const file of files) {
      const name = file.name.replace(/\.[^.]+$/, '');
      toast(`正在解析《${name}》…`);
      let chapters = [];
      try {
        chapters = /\.epub$/i.test(file.name)
          ? await parseEpub(file)
          : splitChapters(decodeBuffer(await file.arrayBuffer()));
      } catch (err) {
        alert(`《${name}》解析失败：${err.message}`);
        continue;
      }
      if (!chapters.length) { alert(`《${name}》没有解析出内容`); continue; }

      const book = {
        id: uid(), title: name, author: '', source: 'local', kind: 'read',
        tags: ['本地导入'],
        chapters: chapters.map(c => ({ id: c.id, t: c.t, content: c.content })),
        words: chapters.reduce((s, c) => s + words(c.content), 0),
        createdAt: Date.now(), updatedAt: Date.now()
      };
      await persistLocal(book);
      state.localBooks.unshift({
        id: book.id, title: book.title, author: '', source: 'local', kind: 'read',
        tags: ['本地导入'], chapters: book.chapters, words: book.words,
        createdAt: book.createdAt, updatedAt: book.updatedAt
      });
      toast(`《${name}》已加入书架：${book.chapters.length} 章`);
    }
    renderShelf();
  }

  /* ------------------------------------------------------------------ 导出 */
  function download(name, text, mime, bom) {
    const blob = new Blob([bom ? '\ufeff' + text : text], { type: mime || 'text/plain;charset=utf-8' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = name;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 3000);
  }

  function bookText(book) {
    return book.chapters.map(c => {
      const body = Array.isArray(c.content) ? c.content.join('\n') : (c.content || '');
      return `${c.t}\n\n${body}`;
    }).join('\n\n\n');
  }

  function exportTxt(book) {
    download(`${book.title}.txt`, bookText(book), 'text/plain;charset=utf-8', true);
  }

  function exportHtml(book) {
    const data = JSON.stringify(book).replace(/</g, '\\u003c');
    const html = `<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>${esc(book.title)}</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{display:flex;height:100vh;font-family:system-ui,"PingFang SC","Microsoft YaHei",sans-serif;background:#f3ede2;color:#2b2823}
aside{width:250px;overflow:auto;border-right:1px solid #e2dacb;padding:14px;background:#fffdf7;flex:0 0 auto}
aside h3{font-size:13px;color:#8b8375;margin-bottom:10px;letter-spacing:1px}
.item{padding:8px 10px;border-radius:6px;cursor:pointer;font-size:14px;color:#555;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.item:hover{background:#f0e9dc}.item.on{background:#e8ddc9;color:#8b4513;font-weight:600}
main{flex:1;display:flex;flex-direction:column;min-width:0}
.bar{display:flex;gap:8px;align-items:center;padding:10px 20px;border-bottom:1px solid #e2dacb;font-size:13px;background:#fffdf7}
.bar button{border:1px solid #ddd3c0;background:#fff;border-radius:6px;padding:5px 11px;cursor:pointer;font:inherit}
.bar button:hover{background:#f0e9dc}
#view{flex:1;overflow:auto;padding:40px 0}
.inner{max-width:760px;margin:0 auto;padding:0 34px}
#view h1{font-size:1.35em;text-align:center;margin-bottom:1.8em}
#view p{text-indent:2em;line-height:1.9;font-size:19px;margin-bottom:.75em;text-align:justify}
@media(max-width:720px){aside{display:none}}
</style></head><body>
<aside><h3>目录</h3><div id="toc"></div></aside>
<main>
  <div class="bar"><b id="bt"></b><span style="flex:1"></span>
    <button onclick="fs(-1)">A-</button><button onclick="fs(1)">A+</button>
    <button onclick="go(i-1)">上一章</button><button onclick="go(i+1)">下一章</button></div>
  <div id="view"><div class="inner" id="inner"></div></div>
</main>
<script>
var BOOK = ${data};
var i = 0, size = 19;
var $ = function (s) { return document.querySelector(s); };
var esc = function (s) { return String(s).replace(/[&<>]/g, function (c) { return {'&':'&amp;','<':'&lt;','>':'&gt;'}[c]; }); };
function render(){
  var c = BOOK.chapters[i];
  var body = Array.isArray(c.content) ? c.content.join('\\n') : c.content;
  $('#inner').innerHTML = '<h1>'+esc(c.t||c.title)+'</h1>' +
    body.split('\\n').filter(function(x){return x.trim();}).map(function(x){return '<p>'+esc(x)+'</p>';}).join('');
  $('#view').scrollTop = 0;
  document.querySelectorAll('.item').forEach(function(e,k){ e.classList.toggle('on', k===i); });
}
function go(n){ i = Math.max(0, Math.min(BOOK.chapters.length-1, n)); render(); }
function fs(d){ size = Math.max(13, size+d); document.querySelectorAll('#inner p').forEach(function(p){ p.style.fontSize=size+'px'; }); }
$('#bt').textContent = BOOK.title;
$('#toc').innerHTML = BOOK.chapters.map(function(c,k){ return '<div class="item" onclick="go('+k+')">'+esc(c.t||c.title)+'</div>'; }).join('');
render();
document.addEventListener('keydown', function(e){ if(e.key==='ArrowLeft')go(i-1); if(e.key==='ArrowRight')go(i+1); });
<\/script></body></html>`;
    download(`${book.title}.html`, html, 'text/html;charset=utf-8');
  }

  /* ------------------------------------------------------------------ 事件 */
  function bindEvents() {
    /* 顶部 */
    $('#btnTheme').onclick = cycleTheme;
    $('#btnFull').onclick = () => {
      if (document.fullscreenElement) document.exitFullscreen();
      else if (document.documentElement.requestFullscreen) document.documentElement.requestFullscreen();
    };
    $('#search').oninput = debounce(e => { state.keyword = e.target.value; renderShelf(); }, 160);
    $('#btnImport').onclick = () => $('#fileInput').click();
    $('#btnImport2').onclick = () => $('#fileInput').click();
    $('#fileInput').onchange = async e => {
      const files = e.target.files;
      e.target.value = '';
      await importFiles(files);
    };
    $('#btnResume').onclick = () => {
      const id = $('#btnResume').dataset.id;
      if (id) setHash('#/b/' + encodeURIComponent(id) + '/');
    };
    $('#btnNew').onclick = async () => {
      const title = (prompt('给你的新书起个名字：', '未命名作品') || '').trim();
      if (!title) return;
      const book = {
        id: uid(), title: title, author: '', source: 'local', kind: 'write',
        tags: ['原创'],
        chapters: [{ id: uid(), t: '第一章', content: '' }],
        words: 0, createdAt: Date.now(), updatedAt: Date.now()
      };
      await persistLocal(book);
      state.localBooks.unshift(book);
      setHash('#/w/' + encodeURIComponent(book.id));
    };

    /* 书架点击 */
    const shelfClick = async e => {
      if (e.target.closest('a')) return;                 // 下载链接等交给浏览器
      const card = e.target.closest('.card, .row');
      if (!card) return;
      const id = card.dataset.id;
      const actEl = e.target.closest('[data-act]');
      const act = actEl ? actEl.dataset.act : null;
      if (act === 'del') {
        const b = findBook(id);
        if (!b) return;
        if (!confirm(`确定从书架删除《${b.title}》吗？删除后无法恢复。`)) return;
        await DB.del(id);
        state.localBooks = state.localBooks.filter(x => x.id !== id);
        renderShelf();
        return;
      }
      if (act === 'write') { setHash('#/w/' + encodeURIComponent(id)); return; }
      setHash('#/b/' + encodeURIComponent(id) + '/');
    };
    $('#shelfPublic').onclick = shelfClick;
    $('#shelfLocal').onclick = shelfClick;
    $('#shelfPublic').onkeydown = e => {
      if (e.key === 'Enter' || e.key === ' ') { const c = e.target.closest('.card'); if (c) { e.preventDefault(); setHash('#/b/' + encodeURIComponent(c.dataset.id) + '/'); } }
    };

    /* 阅读器 */
    $('#btnBack').onclick = () => { saveProgressSoon(); setHash('#/'); };
    $('#btnPrev').onclick = () => gotoChapter(state.reader.idx - 1);
    $('#btnNext').onclick = () => gotoChapter(state.reader.idx + 1);
    $('#btnPrev2').onclick = () => gotoChapter(state.reader.idx - 1);
    $('#btnNext2').onclick = () => gotoChapter(state.reader.idx + 1);
    $('#btnTocToggle').onclick = () => openToc($('#toc'));
    $('#btnTocOpen').onclick = () => openToc($('#toc'));
    $('#tocSearch').oninput = debounce(() => {
      if (state.reader.book) renderToc(state.reader.book);
    }, 140);
    $('#tocList').onclick = e => {
      const item = e.target.closest('.toc-item');
      if (!item) return;
      gotoChapter(+item.dataset.i);
      if (isMobile()) closeToc();
    };
    $('#mask').onclick = closeToc;

    $('#btnFont').onclick = e => {
      e.stopPropagation();
      $('#pop').classList.toggle('on');
    };
    document.addEventListener('click', e => {
      const pop = $('#pop');
      if (pop.classList.contains('on') && !pop.contains(e.target) && e.target !== $('#btnFont')) pop.classList.remove('on');
    });
    $('#setFs').oninput = e => { settings.fs = +e.target.value; applySettings(); };
    $('#setLh').oninput = e => { settings.lh = +e.target.value; applySettings(); };
    $('#segFont').onclick = e => { const b = e.target.closest('button'); if (b) { settings.font = b.dataset.v; applySettings(); } };
    $('#segWidth').onclick = e => { const b = e.target.closest('button'); if (b) { settings.width = +b.dataset.v; applySettings(); } };

    /* 写作器 */
    $('#btnBack2').onclick = async () => { await forceSave(); setHash('#/'); };
    $('#btnWToc').onclick = () => openToc($('#wtoc'));
    ['#wTitle', '#chTitle', '#chBody'].forEach(sel => $(sel).addEventListener('input', () => { scheduleSave(); updateWStat(); }));
    $('#chBody').addEventListener('blur', forceSave);
    $('#wChapters').onclick = async e => {
      const item = e.target.closest('.toc-item');
      if (!item) return;
      const i = +item.dataset.i;
      if (e.target.classList.contains('del')) {
        if (state.writer.chapters.length <= 1) { toast('至少保留一章'); return; }
        if (!confirm('删除这一章？')) return;
        state.writer.chapters.splice(i, 1);
        state.reader.idx = clamp(state.reader.idx, 0, state.writer.chapters.length - 1);
        await forceSave();
        loadWriterChapter(state.reader.idx);
        return;
      }
      await forceSave();
      loadWriterChapter(i);
      closeToc();
    };
    $('#btnAddCh').onclick = async () => {
      await forceSave();
      const b = state.writer;
      b.chapters.push({ id: uid(), t: `第${b.chapters.length + 1}章`, content: '' });
      state.reader.idx = b.chapters.length - 1;
      await forceSave();
      loadWriterChapter(state.reader.idx);
      $('#chTitle').focus();
    };
    $('#btnExportTxt').onclick = async () => { await forceSave(); exportTxt(state.writer); };
    $('#btnExportHtml').onclick = async () => { await forceSave(); exportHtml(state.writer); };
    $('#btnPreview').onclick = async () => { await forceSave(); setHash('#/b/' + encodeURIComponent(state.writer.id) + '/'); };

    /* 全局键盘 */
    document.addEventListener('keydown', e => {
      const tag = (e.target.tagName || '').toUpperCase();
      if (/^(INPUT|TEXTAREA|SELECT)$/.test(tag) || e.target.isContentEditable) return;
      const view = document.body.dataset.view;

      if (view === 'reader') {
        if (e.key === 'ArrowLeft') { gotoChapter(state.reader.idx - 1); e.preventDefault(); }
        else if (e.key === 'ArrowRight') { gotoChapter(state.reader.idx + 1); e.preventDefault(); }
        else if (e.key === 'PageDown' || e.key === ' ') { window.scrollBy({ top: window.innerHeight * 0.9, behavior: 'smooth' }); e.preventDefault(); }
        else if (e.key === 'PageUp') { window.scrollBy({ top: -window.innerHeight * 0.9, behavior: 'smooth' }); e.preventDefault(); }
        else if (e.key === 't' || e.key === 'T') { openToc($('#toc')); }
        else if (e.key === '+' || e.key === '=') { settings.fs = clamp(settings.fs + 1, 15, 32); applySettings(); }
        else if (e.key === '-') { settings.fs = clamp(settings.fs - 1, 15, 32); applySettings(); }
        else if (e.key === 'Escape') {
          if ($('#pop').classList.contains('on')) $('#pop').classList.remove('on');
          else if (isMobile() && $('#toc').classList.contains('open')) closeToc();
          else $('#btnBack').click();
        }
      } else if (view === 'writer' && e.key === 'Escape') {
        $('#btnBack2').click();
      }
    });

    /* 滚动 & 手势 */
    window.addEventListener('scroll', onScroll, { passive: true });
    window.addEventListener('beforeunload', () => {
      const b = state.reader.book;
      if (b && document.body.dataset.view === 'reader') {
        const max = document.documentElement.scrollHeight - window.innerHeight;
        setProgress(b.id, state.reader.idx, max > 0 ? clamp(window.scrollY / max, 0, 1) : 0);
      }
      store.set('modu-progress', progAll);
    });

    (function bindSwipe() {
      let sx = 0, sy = 0, active = false;
      const main = $('.read-main');
      main.addEventListener('touchstart', e => {
        if (!isMobile() || document.body.dataset.view !== 'reader') return;
        sx = e.touches[0].clientX; sy = e.touches[0].clientY; active = true;
      }, { passive: true });
      main.addEventListener('touchend', e => {
        if (!active) return;
        active = false;
        const dx = e.changedTouches[0].clientX - sx;
        const dy = e.changedTouches[0].clientY - sy;
        if (Math.abs(dx) > 64 && Math.abs(dx) > Math.abs(dy) * 1.6) {
          gotoChapter(state.reader.idx + (dx < 0 ? 1 : -1));
        }
      }, { passive: true });
    })();

    window.addEventListener('hashchange', () => route());
    window.addEventListener('resize', debounce(() => {
      if (!isMobile()) closeToc();
    }, 200));
  }

  /* ------------------------------------------------------------------ 启动 */
  let started = false;
  async function init() {
    if (started) return;
    started = true;
    // 首次访问跟随系统主题
    if (!store.get('modu-settings', null) && window.matchMedia &&
        window.matchMedia('(prefers-color-scheme: dark)').matches) {
      settings.theme = 'dark';
    }
    applySettings();

    state.publicBooks = (window.MD_PUBLIC_BOOKS || []).map(normalizePublic);

    try { await DB.open(); } catch (e) { console.warn('IndexedDB 不可用', e); }
    await loadLocalBooks();

    bindEvents();
    renderShelf();

    if (parseRoute().view !== 'shelf') route();
    else showView('shelf');
  }

  document.addEventListener('DOMContentLoaded', init);
  if (document.readyState !== 'loading') init();
})();
