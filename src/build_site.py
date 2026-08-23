# -*- coding: utf-8 -*-
"""
data/days/*.json から静的サイト(docs/)を生成する。
  docs/index.html                  … トップ(入口)。site/home.json の項目をカードで並べる
  docs/toolbox/index.html          … 薬剤師ツールボックス(最新の更新まとめ + ツール一覧)
  docs/watch/index.html            … 添付文書ウォッチ: 最新日
  docs/watch/days/YYYY-MM-DD.html  … 日付ごとのページ(バックナンバー)
  docs/watch/archive.html          … バックナンバー一覧
  docs/watch/search.html + search.json … 薬名・企業名・一般名で検索
  docs/assets/style.css            … 共通デザイン
  docs/tools/*.html                … 手作りのツール(計算機など)。ここは生成対象外、読むだけ
  ※ watch/ toolbox/ assets/ index.html 以外は書き換えない
"""
from __future__ import annotations

import difflib
import html
import json
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

JST = timezone(timedelta(hours=9))
WEEK_URL = "https://www.info.pmda.go.jp/downfiles/ph/1week.html"
HOME_TITLE = "ホーム"                 # トップ(入口)ページの名前。site/home.json の title で上書き可
SITE_TITLE = "薬剤師ツールボックス"  # 薬剤師ツールボックス(添付文書ウォッチ+ツール)の名前
WATCH_TITLE = "添付文書ウォッチ"

# トップページに並べる項目の既定値(site/home.json があればそちらを使う)
DEFAULT_HOME = {
    "title": HOME_TITLE,
    "lead": "ここから各機能へ。",
    "sections": [
        {"emoji": "💊", "title": SITE_TITLE, "desc": "添付文書ウォッチ・各種計算ツール",
         "href": "toolbox/index.html", "live": "toolbox"},
    ],
}


def load_home(root: Path) -> dict:
    """site/home.json(トップページの項目一覧)を読む。無ければ既定値"""
    f = root / "site" / "home.json"
    cfg = dict(DEFAULT_HOME)
    if f.exists():
        try:
            cfg.update(json.loads(f.read_text(encoding="utf-8")))
        except Exception as e:  # noqa: BLE001
            print(f"!! site/home.json を読めませんでした({e})。既定値で生成します")
    return cfg

# 重要とみなす項目(見出しにこれらを含む改訂はマークする)
IMPORTANT_KEYS = ["警告", "禁忌", "効能", "用法", "重要な基本的注意", "特定の背景",
                  "相互作用", "副作用", "過量投与", "適用上の注意", "組成"]

WEEKDAYS = "月火水木金土日"


def esc(s) -> str:
    return html.escape("" if s is None else str(s), quote=True)


def fmt_date(d: str) -> str:
    y, m, dd = d.split("-")
    wd = WEEKDAYS[datetime(int(y), int(m), int(dd)).weekday()]
    return f"{y}年{int(m)}月{int(dd)}日({wd})"


def nl2br(s: str) -> str:
    return esc(s).replace("\n", "<br>")


def char_diff(a: str, b: str) -> tuple[str, str]:
    """文字単位の差分を <del>/<ins> 付きHTMLで(before側, after側)"""
    if len(a) + len(b) > 6000:
        return nl2br(a), nl2br(b)
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    ba, bb = [], []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            ba.append(nl2br(a[i1:i2]))
            bb.append(nl2br(b[j1:j2]))
        elif op == "delete":
            ba.append(f"<del>{nl2br(a[i1:i2])}</del>")
        elif op == "insert":
            bb.append(f"<ins>{nl2br(b[j1:j2])}</ins>")
        else:
            ba.append(f"<del>{nl2br(a[i1:i2])}</del>")
            bb.append(f"<ins>{nl2br(b[j1:j2])}</ins>")
    return "".join(ba), "".join(bb)


def is_important(path) -> bool:
    top = path[0] if path else ""
    return any(k in top for k in IMPORTANT_KEYS)


def path_html(path) -> str:
    if not path:
        return '<span class="path">(見出しなし)</span>'
    return '<span class="path">' + " › ".join(esc(p) for p in path) + "</span>"


# ---------------------------------------------------------------- ページ共通
def scan_tools(docs: Path) -> list[dict]:
    """docs/tools/*.html を見て [{file, title, desc}] を返す(先頭が _ のファイルは除外)"""
    out = []
    tdir = docs / "tools"
    if not tdir.exists():
        return out
    for f in sorted(tdir.glob("*.html")):
        if f.name.startswith("_"):
            continue
        txt = f.read_text(encoding="utf-8", errors="replace")
        m = re.search(r"<title>(.*?)</title>", txt, flags=re.S | re.I)
        title = html.unescape(m.group(1)).split("|")[0].strip() if m else f.stem
        m2 = re.search(r'<meta\s+name="description"\s+content="(.*?)"', txt, flags=re.S | re.I)
        desc = html.unescape(m2.group(1)).strip() if m2 else ""
        out.append({"file": f.name, "title": title, "desc": desc})
    return out


def top_nav(rel: str, tools: list[dict]) -> str:
    """rel: docs/ ルートへの相対パス。全ページ共通のヘッダー"""
    tool_links = "".join(f'<a href="{rel}tools/{t["file"]}">{esc(t["title"])}</a>' for t in tools)
    tools_menu = f'<details class="menu"><summary>🧮 ツール</summary><div class="dd">{tool_links}</div></details>' if tools else ""
    return f"""<header class="top">
  <a class="brand" href="{rel}index.html">🏠 {HOME_TITLE}</a>
  <nav class="topnav">
    <a href="{rel}toolbox/index.html">💊 {SITE_TITLE}</a>
    <a href="{rel}watch/index.html">📄 {WATCH_TITLE}</a>
    <a href="{rel}watch/archive.html">バックナンバー</a>
    <a href="{rel}watch/search.html">検索</a>
    {tools_menu}
  </nav>
</header>"""


def layout(title: str, body: str, rel: str, nav_days: list[str], current: str | None,
           built_at: str, tools: list[dict], side: bool = True) -> str:
    """rel: このページから docs/ ルートへの相対パス('' / '../' / '../../')"""
    nav = "".join(
        f'<li><a href="{rel}watch/days/{d}.html"{" class=\"cur\"" if d == current else ""}>{fmt_date(d)}</a></li>'
        for d in nav_days[:14]
    )
    aside = f"""<aside class="side">
  <h4>最近の日付</h4>
  <ul class="daylist">{nav}</ul>
  <p class="small"><a href="{rel}watch/archive.html">すべて見る →</a> ｜ <a href="{WEEK_URL}" target="_blank" rel="noopener">PMDA元ページ ↗</a></p>
</aside>""" if side else ""
    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>{esc(title)}</title>
<link rel="stylesheet" href="{rel}assets/style.css">
</head>
<body>
{top_nav(rel, tools)}
<div class="wrap">
{aside}
<main class="main">
{body}
<footer class="foot">
  <p>個人用サイト。添付文書ウォッチの出典: PMDA「過去1週間以内に更新された添付文書情報」および各添付文書XML。最終生成: {esc(built_at)}</p>
</footer>
</main>
</div>
</body>
</html>
"""


# ---------------------------------------------------------------- 1件の表示
def render_entry(e: dict) -> tuple[str, bool]:
    """(html, important_flag)"""
    meta = e.get("meta") or {}
    brands = meta.get("brands") or []
    title = " / ".join(brands) if brands else e.get("brand", "")
    reason = e.get("reason", "")
    badge_cls = {"更新": "upd", "新規": "new"}.get(reason, "oth")
    revised = e.get("revised") or []
    diff = e.get("diff")
    imp = any(is_important(r.get("path") or []) for r in revised)
    if diff and diff.get("changes"):
        imp = imp or any(is_important((c.get("path") or "").split(" > ")) for c in diff["changes"])

    rev = meta.get("revision") or {}
    cur, prev = rev.get("current") or {}, rev.get("previous") or {}
    rev_txt = ""
    if cur.get("ym") or cur.get("ver"):
        rev_txt = f"{esc(cur.get('ym') or '')} {esc(cur.get('ver') or '')}".strip()
        if prev.get("ym") or prev.get("ver"):
            rev_txt += f"(前回 {esc(prev.get('ym') or '')} {esc(prev.get('ver') or '')})"

    links = []
    if e.get("pack_url"):
        links.append(f'<a href="{esc(e["pack_url"])}" target="_blank" rel="noopener">PMDAページ</a>')
    if e.get("pdf_url"):
        links.append(f'<a href="{esc(e["pdf_url"])}" target="_blank" rel="noopener">PDF</a>')

    metas = []
    if e.get("company"):
        metas.append(f'<span>🏢 {esc(e["company"])}</span>')
    if meta.get("generic_name"):
        metas.append(f'<span>一般名: {esc(meta["generic_name"])}</span>')
    if meta.get("therapeutic_class"):
        metas.append(f'<span>{esc(meta["therapeutic_class"])}</span>')
    if rev_txt:
        metas.append(f'<span>改訂: {rev_txt}</span>')
    if links:
        metas.append(" ".join(links))

    parts = [f'<article class="entry{" imp" if imp else ""}" id="{esc(e.get("packins_no") or "")}">']
    parts.append(f'<h3><span class="badge {badge_cls}">{esc(reason)}</span> {esc(title)}'
                 + (' <span class="impmark" title="安全性・用法など重要項目の改訂を含む">⚠ 重要項目</span>' if imp else "")
                 + "</h3>")
    parts.append('<div class="meta">' + " ｜ ".join(metas) + "</div>")

    if e.get("error"):
        parts.append(f'<p class="err">⚠ 取得エラー: {esc(e["error"])}(翌朝に自動で再試行します。急ぐ場合はPMDAページを直接ご確認ください)</p>')
    if e.get("note"):
        parts.append(f'<p class="small">ℹ {esc(e["note"])}</p>')

    # 今回改訂箇所
    parts.append("<details open><summary>今回改訂箇所(添付文書内の「＊＊」印)"
                 f" — {len(revised)}件</summary>")
    if revised:
        parts.append('<ul class="rev">')
        for r in revised:
            p = r.get("path") or []
            cls = " imp" if is_important(p) else ""
            parts.append(f'<li class="{cls.strip()}">{path_html(p)}<div class="txt">{nl2br(r.get("text") or "")}</div></li>')
        parts.append("</ul>")
    else:
        if reason == "新規":
            parts.append('<p class="small">新規掲載のため改訂マークはありません。</p>')
        elif (cur.get("ver") or "") == "第1版":
            parts.append('<p class="small">第1版(新しく作成された添付文書)のため改訂マークはありません。</p>')
        else:
            parts.append('<p class="small">改訂マーク(＊＊)は付いていません(版を変えない再掲載・軽微な修正などの可能性)。'
                         "下の差分を参考にしてください。</p>")
    parts.append("</details>")

    # 差分
    if diff is None:
        parts.append('<details><summary>前回取得分との差分 — 初回取得のためなし'
                     "(次にこの添付文書が更新されたときから表示されます)</summary></details>")
    else:
        ch = diff.get("changes") or []
        when = diff.get("prev_seen_date") or diff.get("prev_fetched_at") or ""
        if not ch:
            parts.append(f'<details><summary>前回取得分({esc(when)} / {esc(diff.get("prev_packins_no") or "")})との差分 — 本文に変更なし</summary></details>')
        else:
            parts.append(f'<details open><summary>前回取得分({esc(when)} / {esc(diff.get("prev_packins_no") or "")})との差分 — {len(ch)}箇所</summary>')
            parts.append('<ul class="diff">')
            for c in ch:
                p = (c.get("path") or "").split(" > ") if c.get("path") else []
                cls = "imp" if is_important(p) else ""
                kind = c.get("kind")
                label = {"changed": "変更", "added": "追加", "removed": "削除"}.get(kind, kind)
                parts.append(f'<li class="{cls}"><span class="kind {kind}">{label}</span> {path_html(p)}')
                if kind == "changed":
                    a, b = char_diff(c.get("before") or "", c.get("after") or "")
                    parts.append(f'<div class="before"><span class="lbl">前</span>{a}</div>'
                                 f'<div class="after"><span class="lbl">後</span>{b}</div>')
                elif kind == "added":
                    parts.append(f'<div class="after"><ins>{nl2br(c.get("after") or "")}</ins></div>')
                else:
                    parts.append(f'<div class="before"><del>{nl2br(c.get("before") or "")}</del></div>')
                parts.append("</li>")
            parts.append("</ul></details>")
    parts.append("</article>")
    return "\n".join(parts), imp


def render_day(day: dict) -> str:
    ups = day.get("updates") or []
    dels = day.get("deletes") or []
    n_upd = sum(1 for u in ups if u.get("reason") == "更新")
    n_new = sum(1 for u in ups if u.get("reason") == "新規")
    n_oth = len(ups) - n_upd - n_new
    rendered = [render_entry(u) for u in ups]
    n_imp = sum(1 for _, i in rendered if i)

    out = [f"<h1>{fmt_date(day['date'])} の更新</h1>"]
    out.append('<div class="stats">'
               f'<span class="badge upd">更新 {n_upd}</span> <span class="badge new">新規 {n_new}</span> '
               + (f'<span class="badge oth">その他 {n_oth}</span> ' if n_oth else "")
               + f'<span class="badge del">削除 {len(dels)}</span> '
               + (f'<span class="badge imp">⚠ 重要項目あり {n_imp}</span>' if n_imp else "")
               + "</div>")
    if ups:
        # 目次(重要を先に)
        out.append('<details class="toc"><summary>この日の一覧(クリックでジャンプ)</summary><ol>')
        order = sorted(range(len(ups)), key=lambda i: (not rendered[i][1], i))
        for i in order:
            u = ups[i]
            meta = u.get("meta") or {}
            t = " / ".join(meta.get("brands") or []) or u.get("brand", "")
            out.append(f'<li><a href="#{esc(u.get("packins_no") or "")}">{esc(t)}</a>'
                       f' <span class="small">{esc(u.get("reason", ""))}{" ⚠" if rendered[i][1] else ""}</span></li>')
        out.append("</ol></details>")
        for i in order:
            out.append(rendered[i][0])
    else:
        out.append("<p>この日は掲載分がありませんでした。</p>")
    if dels:
        out.append("<h2>削除分</h2><table class=\"del\"><tr><th>販売名</th><th>企業名</th><th>理由</th></tr>")
        for d in dels:
            out.append(f"<tr><td>{esc(d.get('brand'))}</td><td>{esc(d.get('company'))}</td><td>{esc(d.get('reason'))}</td></tr>")
        out.append("</table>")
    return "\n".join(out)


# ---------------------------------------------------------------- 生成
CSS = """
:root{--bg:#fff;--fg:#222;--mut:#666;--line:#e3e3e3;--card:#fafafa;--acc:#2a62b8;--imp:#b8321a;--ins:#e6ffed;--del:#ffeef0;--insfg:#116329;--delfg:#82071e}
@media (prefers-color-scheme:dark){:root{--bg:#15171a;--fg:#e6e6e6;--mut:#a0a0a0;--line:#333;--card:#1e2125;--acc:#7fb0ff;--imp:#ff8a70;--ins:#12301b;--del:#3a1418;--insfg:#9be3ad;--delfg:#ffb3bb}}
*{box-sizing:border-box}body{margin:0;font-family:system-ui,-apple-system,"Segoe UI","Hiragino Sans","Yu Gothic UI",Meiryo,sans-serif;background:var(--bg);color:var(--fg);line-height:1.6}
a{color:var(--acc)}
.top{display:flex;flex-wrap:wrap;gap:.5rem 1rem;align-items:center;padding:.6rem 1rem;border-bottom:1px solid var(--line);position:sticky;top:0;background:var(--bg);z-index:5}
.brand{font-weight:700;text-decoration:none;color:var(--fg);font-size:1.1rem}
.topnav a{margin-right:.9rem;text-decoration:none}
.wrap{display:flex;gap:1.5rem;max-width:1200px;margin:0 auto;padding:1rem}
.side{flex:0 0 200px}.side h4{margin:.2rem 0 .4rem}.daylist{list-style:none;padding:0;margin:0}.daylist li{margin:.15rem 0}.daylist a.cur{font-weight:700}
.main{flex:1;min-width:0}
@media (max-width:800px){.wrap{flex-direction:column}.side{flex:none}.daylist{display:flex;flex-wrap:wrap;gap:.3rem .8rem}}
h1{font-size:1.5rem;margin:.2rem 0 .6rem}h2{font-size:1.2rem;margin-top:2rem}h3{font-size:1.05rem;margin:0 0 .3rem}
.badge{display:inline-block;padding:.05rem .5rem;border-radius:.6rem;font-size:.8rem;color:#fff;background:#888;vertical-align:middle}
.badge.upd{background:#2a62b8}.badge.new{background:#1f8f4e}.badge.del{background:#777}.badge.oth{background:#a06a00}.badge.imp{background:var(--imp)}
.stats{margin:.3rem 0 1rem}
.entry{border:1px solid var(--line);border-radius:.6rem;padding:.8rem 1rem;margin:1rem 0;background:var(--card)}
.entry.imp{border-left:5px solid var(--imp)}
.impmark{color:var(--imp);font-size:.85rem;font-weight:600}
.meta{font-size:.85rem;color:var(--mut);margin-bottom:.5rem}
details{margin:.4rem 0}summary{cursor:pointer;font-weight:600}
ul.rev,ul.diff{padding-left:1.1rem}ul.rev li,ul.diff li{margin:.5rem 0;padding:.4rem .6rem;border-left:3px solid var(--line);background:var(--bg);border-radius:.3rem}
ul.rev li.imp,ul.diff li.imp{border-left-color:var(--imp)}
.path{font-weight:600;font-size:.9rem}.txt{white-space:normal;margin-top:.2rem;font-size:.95rem}
.kind{font-size:.75rem;color:#fff;background:#888;padding:0 .4rem;border-radius:.4rem;margin-right:.3rem}.kind.changed{background:#a06a00}.kind.added{background:#1f8f4e}.kind.removed{background:#b8321a}
.before,.after{margin:.25rem 0;padding:.3rem .5rem;border-radius:.3rem;font-size:.93rem}.before{background:var(--del)}.after{background:var(--ins)}
.lbl{display:inline-block;font-size:.75rem;color:var(--mut);margin-right:.4rem}
ins{background:var(--ins);color:var(--insfg);text-decoration:none;font-weight:600}del{background:var(--del);color:var(--delfg)}
.err{color:var(--imp)}.small{font-size:.85rem;color:var(--mut)}
table.del,table.arch{border-collapse:collapse;width:100%;font-size:.92rem}table.del td,table.del th,table.arch td,table.arch th{border:1px solid var(--line);padding:.3rem .5rem;text-align:left}
.toc ol{margin:.3rem 0;padding-left:1.4rem}
.foot{margin-top:3rem;font-size:.8rem;color:var(--mut);border-top:1px solid var(--line);padding-top:.6rem}
#q{width:100%;max-width:500px;font-size:1rem;padding:.4rem .6rem;border:1px solid var(--line);border-radius:.4rem;background:var(--bg);color:var(--fg)}
.hit{padding:.4rem 0;border-bottom:1px solid var(--line)}
.menu{display:inline-block;position:relative;margin-right:.9rem}.menu summary{font-weight:400;color:var(--acc);list-style:none}.menu summary::-webkit-details-marker{display:none}
.menu .dd{position:absolute;top:1.6rem;left:0;background:var(--bg);border:1px solid var(--line);border-radius:.4rem;padding:.4rem .8rem;min-width:12rem;box-shadow:0 4px 14px rgba(0,0,0,.12);z-index:9}.menu .dd a{display:block;margin:.2rem 0}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:.8rem;margin:.6rem 0 1.2rem}
.card{display:block;border:1px solid var(--line);border-radius:.6rem;padding:.8rem 1rem;background:var(--card);text-decoration:none;color:var(--fg)}.card:hover{border-color:var(--acc)}.card b{color:var(--acc)}.card .small{display:block;margin-top:.2rem}.card.big{padding:1.2rem 1.2rem;font-size:1.05rem}.card.big b{font-size:1.2rem}
.calc{max-width:640px}.calc label{display:block;margin:.6rem 0 .2rem;font-weight:600}.calc input,.calc select{font-size:1rem;padding:.35rem .5rem;border:1px solid var(--line);border-radius:.4rem;background:var(--bg);color:var(--fg);width:100%;max-width:320px}
.calc .result{margin-top:1rem;padding:.8rem 1rem;border-radius:.5rem;background:var(--card);border:1px solid var(--line);font-size:1.05rem}.calc .result b{font-size:1.3rem}
"""

SEARCH_JS = """
<h1>検索</h1>
<p class="small">販売名・一般名・企業名・改訂された項目名で絞り込めます(記録済みの全日付が対象)。</p>
<input id="q" type="search" placeholder="例: アリピプラゾール / 禁忌 / MSD" autofocus>
<p class="small" id="cnt"></p>
<div id="res"></div>
<script>
(async function(){
  const data = await (await fetch('search.json')).json();
  const q=document.getElementById('q'), res=document.getElementById('res'), cnt=document.getElementById('cnt');
  const norm=s=>(s||'').toString().normalize('NFKC').toLowerCase();
  function run(){
    const kw=norm(q.value).trim().split(/\\s+/).filter(Boolean);
    let hits=data;
    if(kw.length){hits=data.filter(e=>{const h=norm(e.t+' '+e.g+' '+e.c+' '+e.p);return kw.every(k=>h.includes(k));});}
    hits=hits.slice(0,300);
    cnt.textContent=kw.length?`${hits.length}件(最大300件表示)`:`全${data.length}件`;
    res.innerHTML=hits.map(e=>`<div class="hit"><a href="days/${e.d}.html#${e.k}">${e.d}</a> <span class="badge ${e.r==='更新'?'upd':e.r==='新規'?'new':'oth'}">${e.r}</span> <b>${esc(e.t)}</b>${e.i?' <span class="impmark">⚠</span>':''}<br><span class="small">${esc(e.g)} ｜ ${esc(e.c)}${e.p?' ｜ 改訂: '+esc(e.p):''}</span></div>`).join('');
  }
  function esc(s){return (s||'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}
  q.addEventListener('input',run);
  if(location.hash){q.value=decodeURIComponent(location.hash.slice(1));}
  run();
})();
</script>
"""


def toolbox_live(days: list[dict]) -> str:
    """トップのカードに出す一行(最新の更新状況)"""
    if not days:
        return "まだデータがありません"
    d = days[0]
    ups = d.get("updates") or []
    n_imp = sum(1 for u in ups if render_entry(u)[1])
    return (f"{WATCH_TITLE}: {fmt_date(d['date'])} 更新{sum(1 for u in ups if u.get('reason') == '更新')}件・"
            f"新規{sum(1 for u in ups if u.get('reason') == '新規')}件" + (f"・⚠重要{n_imp}件" if n_imp else ""))


def render_home(cfg: dict, days: list[dict], tools: list[dict]) -> str:
    out = [f"<h1>{esc(cfg.get('title') or HOME_TITLE)}</h1>"]
    if cfg.get("lead"):
        out.append(f'<p class="small">{esc(cfg["lead"])}</p>')
    cards = []
    for sec in cfg.get("sections") or []:
        live = ""
        if sec.get("live") == "toolbox":
            live = f'<span class="small">{esc(toolbox_live(days))}</span>'
        elif sec.get("live"):
            live = f'<span class="small">{esc(sec["live"])}</span>'
        cards.append(f'<a class="card big" href="{esc(sec.get("href") or "#")}">'
                     f'<b>{esc(sec.get("emoji") or "")} {esc(sec.get("title") or "")}</b>'
                     f'<span class="small">{esc(sec.get("desc") or "")}</span>{live}</a>')
    out.append('<div class="cards">' + "".join(cards) + "</div>")
    out.append('<p class="small">項目の追加・並べ替えは <code>site/home.json</code> を編集(README参照)。</p>')
    return "\n".join(out)


def render_toolbox(days: list[dict], tools: list[dict]) -> str:
    out = [f"<h1>💊 {SITE_TITLE}</h1>"]
    out.append(f'<h2>📄 {WATCH_TITLE}</h2>')
    if days:
        d = days[0]
        ups = d.get("updates") or []
        n_upd = sum(1 for u in ups if u.get("reason") == "更新")
        n_new = sum(1 for u in ups if u.get("reason") == "新規")
        imp_items = []
        for u in ups:
            meta = u.get("meta") or {}
            _, imp = render_entry(u)
            if imp:
                imp_items.append((u.get("packins_no") or "", " / ".join(meta.get("brands") or []) or u.get("brand", "")))
        out.append(f'<p>最新の記録日: <a href="watch/days/{d["date"]}.html"><b>{fmt_date(d["date"])}</b></a> ｜ '
                   f'<span class="badge upd">更新 {n_upd}</span> <span class="badge new">新規 {n_new}</span> '
                   f'<span class="badge del">削除 {len(d.get("deletes") or [])}</span>'
                   + (f' <span class="badge imp">⚠ 重要項目 {len(imp_items)}</span>' if imp_items else "") + "</p>")
        if imp_items:
            out.append("<ul>" + "".join(f'<li><a href="watch/days/{d["date"]}.html#{esc(k)}">{esc(t)}</a></li>' for k, t in imp_items[:15]) + "</ul>")
        out.append('<p><a href="watch/index.html">最新の詳細を見る →</a> ｜ <a href="watch/archive.html">バックナンバー</a> ｜ <a href="watch/search.html">検索</a></p>')
        out.append('<h3 class="small">最近の日付</h3><ul class="daylist">' + "".join(
            f'<li><a href="watch/days/{x["date"]}.html">{fmt_date(x["date"])}</a> <span class="small">({len(x.get("updates") or [])}件)</span></li>' for x in days[:7]) + "</ul>")
    else:
        out.append("<p>まだデータがありません(初回の自動実行をお待ちください)。</p>")
    out.append("<h2>🧮 ツール</h2>")
    if tools:
        out.append('<div class="cards">' + "".join(
            f'<a class="card" href="tools/{esc(t["file"])}"><b>{esc(t["title"])}</b><span class="small">{esc(t["desc"])}</span></a>' for t in tools) + "</div>")
    else:
        out.append('<p class="small">準備中(docs/tools/ にHTMLを置くとここに自動で並びます)。</p>')
    return "\n".join(out)


def build(root: Path) -> None:
    days_dir = root / "data" / "days"
    docs = root / "docs"
    watch = docs / "watch"
    (watch / "days").mkdir(parents=True, exist_ok=True)
    (docs / "assets").mkdir(parents=True, exist_ok=True)
    (docs / "tools").mkdir(parents=True, exist_ok=True)
    built_at = datetime.now(JST).strftime("%Y-%m-%d %H:%M JST")
    files = sorted(days_dir.glob("*.json"), reverse=True)
    days = [json.loads(f.read_text(encoding="utf-8")) for f in files]
    dates = [d["date"] for d in days]
    tools = scan_tools(docs)

    (docs / "assets" / "style.css").write_text(CSS.strip() + "\n", encoding="utf-8")
    (docs / ".nojekyll").write_text("", encoding="utf-8")
    # 手作りページ(tools/)用: <div id="site-nav" data-rel="../"></div> に共通ヘッダーを差し込むJS
    nav_js = ("// 自動生成: build_site.py\n"
              "(function(){var el=document.getElementById('site-nav');if(!el)return;"
              "var rel=el.getAttribute('data-rel')||'';"
              "var h=" + json.dumps(top_nav("__REL__", tools), ensure_ascii=False) + ";"
              "el.outerHTML=h.split('__REL__').join(rel);})();\n")
    (docs / "assets" / "nav.js").write_text(nav_js, encoding="utf-8")

    search_rows = []
    arch_rows = []
    for d in days:
        body = render_day(d)
        page = layout(f"{fmt_date(d['date'])} の更新", body, "../../", dates, d["date"], built_at, tools)
        (watch / "days" / f"{d['date']}.html").write_text(page, encoding="utf-8")
        n_imp = 0
        for u in d.get("updates") or []:
            meta = u.get("meta") or {}
            paths = [" > ".join(r.get("path") or []) for r in (u.get("revised") or [])]
            imp = any(is_important(r.get("path") or []) for r in (u.get("revised") or []))
            if (u.get("diff") or {}).get("changes"):
                imp = imp or any(is_important((c.get("path") or "").split(" > ")) for c in u["diff"]["changes"])
            n_imp += int(imp)
            search_rows.append({
                "d": d["date"], "k": u.get("packins_no") or "",
                "t": " / ".join(meta.get("brands") or []) or u.get("brand", ""),
                "g": meta.get("generic_name") or "", "c": u.get("company") or "",
                "r": u.get("reason") or "", "p": " / ".join(sorted(set(p for p in paths if p))),
                "i": imp,
            })
        ups = d.get("updates") or []
        arch_rows.append((d["date"],
                          sum(1 for u in ups if u.get("reason") == "更新"),
                          sum(1 for u in ups if u.get("reason") == "新規"),
                          len(d.get("deletes") or []), n_imp))

    if days:
        latest = days[0]
        body = f'<p class="small">最新の記録日: {fmt_date(latest["date"])}(PMDAの掲載は通常、営業日ごと)</p>' + render_day(latest)
        (watch / "index.html").write_text(layout(WATCH_TITLE, body, "../", dates, latest["date"], built_at, tools), encoding="utf-8")
    else:
        (watch / "index.html").write_text(layout(WATCH_TITLE, "<h1>まだデータがありません</h1>", "../", dates, None, built_at, tools), encoding="utf-8")

    rows = "".join(
        f'<tr><td><a href="days/{d}.html">{fmt_date(d)}</a></td><td>{a}</td><td>{b}</td><td>{c}</td><td>{("⚠ " + str(i)) if i else "-"}</td></tr>'
        for d, a, b, c, i in arch_rows)
    body = ("<h1>バックナンバー</h1>"
            '<table class="arch"><tr><th>日付</th><th>更新</th><th>新規</th><th>削除</th><th>重要項目</th></tr>'
            + rows + "</table>")
    (watch / "archive.html").write_text(layout("バックナンバー", body, "../", dates, None, built_at, tools), encoding="utf-8")

    (watch / "search.json").write_text(json.dumps(search_rows, ensure_ascii=False), encoding="utf-8")
    (watch / "search.html").write_text(layout("検索", SEARCH_JS, "../", dates, None, built_at, tools), encoding="utf-8")

    (docs / "toolbox").mkdir(parents=True, exist_ok=True)
    (docs / "toolbox" / "index.html").write_text(
        layout(SITE_TITLE, render_toolbox(days, tools).replace('href="watch/', 'href="../watch/').replace('href="tools/', 'href="../tools/'),
               "../", dates, None, built_at, tools, side=False), encoding="utf-8")
    home = load_home(root)
    (docs / "index.html").write_text(
        layout(home.get("title") or HOME_TITLE, render_home(home, days, tools), "", dates, None, built_at, tools, side=False),
        encoding="utf-8")
    print(f"site built: {len(days)} days, {len(search_rows)} entries, {len(tools)} tools -> {docs}")


if __name__ == "__main__":
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent
    build(root)
