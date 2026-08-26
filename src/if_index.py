# -*- coding: utf-8 -*-
"""
PMDA「医療用医薬品 添付文書等情報検索」から 一覧を集めて data/*.json に保存する。
  doc="if"    … インタビューフォーム(IF)の一覧 → data/if_index.json(「インタビューフォーム検索」ページの元データ)
  doc="tenpu" … 添付文書の一覧               → data/tenpu_index.json(「添付文書検索」ページの元データ)

やり方(両方共通):
  - PMDAの検索は1回1000件までしか返さないので、薬効分類(2桁)ごとに検索し、
    1000件を超えた分類だけ3桁の小分類に分けて検索し直す
  - 結果は100件/ページなので、ページ送り(PageChangeRequest)で全ページを取る
  - 表示する文書のチェックを IF / 添付文書 に切り替える → 一般名/販売名/企業/PDFリンクが取れる

使い方:  python src/if_index.py             (リポジトリ直下の data/if_index.json を更新)
         python src/if_index.py --tenpu     (同 data/tenpu_index.json を更新)
         python src/run.py --if-index / --tenpu-index   (同上 + サイト生成)
※ 毎日 00:15 JST に GitHub Actions(if-index.yml)で両方とも自動実行。
  PMDAへのアクセスは IF 150〜200回・添付文書 400〜700回程度・1秒待ちつき。
"""
from __future__ import annotations

import html
import http.cookiejar
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

JST = timezone(timedelta(hours=9))
UA = "pmda-tenpu-watch/1.0 (personal use; polite crawler)"
SLEEP = 1.0        # サーバーにやさしく(秒)
ROWS_PER_PAGE = 100
LIMIT = 1000       # PMDA側の検索結果上限

FORM_URL = "https://www.pmda.go.jp/PmdaSearch/iyakuSearch/"
SEARCH_URL = "https://www.pmda.go.jp/PmdaSearch/iyakuSearch"
PAGE_URL = "https://www.pmda.go.jp/PmdaSearch/iyakuSearch/PageChangeRequest/{page}"
DETAIL_BASE = "https://www.pmda.go.jp/PmdaSearch/iyakuDetail/GeneralList/"
IF_BASE = "https://www.info.pmda.go.jp/go/interview/"   # IFのPDFは大体ここ(…/1/xxx.pdf, …/2/xxx.pdf)。違うものは丸ごとURLで持つ

# 「表示する文書」チェックボックス(doc の種類ごとに1つだけチェックする)
DOC_FIELDS = {"if": ("dispColumnsList[2]", "3"),      # インタビューフォーム
              "tenpu": ("dispColumnsList[0]", "1")}   # 添付文書
OUT_NAMES = {"if": "if_index.json", "tenpu": "tenpu_index.json"}
DOC_LABELS = {"if": "インタビューフォーム", "tenpu": "添付文書"}


def log(msg: str) -> None:
    print(f"[{datetime.now(JST).strftime('%H:%M:%S')}] {msg}", flush=True)


# ---------------------------------------------------------------- 検索フォームの項目
def search_fields(effect: str = "", name: str = "", page: int | None = None,
                  date_from: str = "", date_to: str = "", doc: str = "if") -> list[tuple[str, str]]:
    """PMDAの検索フォームと同じ項目一式。全部そろっていないとサーバーが検索してくれない。
    page を指定するとページ送り用(PageChangeRequest)の形になる。doc は表示する文書("if"/"tenpu")。
    ※ docs/if/ docs/tenpu/ の「PMDAで最新を検索」ボタンも同じ項目を送っている(build_site.py 参照)"""
    f = [
        ("ListRows", str(ROWS_PER_PAGE)),
        ("nameWord", name),
        ("iyakuHowtoNameSearchRadioValue", "1"),   # 一般名及び販売名
        ("howtoMatchRadioValue", "1"),             # 部分一致
        ("effectValue", effect),                   # 薬効分類
        ("infoindicationsorefficacy", ""), ("infoindicationsorefficacyHowtoSearch", "and"),
        ("warnings", ""), ("warningsHowtoSearch", "and"),
        ("contraindicationsAvoidedadministration", ""), ("contraindicationsAvoidedadministrationHowtoSearch", "and"),
        ("contraindicatedcombinationPrecautionsforcombination", ""), ("contraindicatedcombinationPrecautionsforcombinationHowtoSearch", "and"),
        DOC_FIELDS[doc],                           # 表示する文書: IF か 添付文書 のどちらか1つ
        ("tglOpFlg", ""), ("updateDocFrDt", date_from), ("updateDocToDt", date_to), ("compNameWord", ""),   # 更新年月日(YYYYMMDD)で絞る(上限超え対策)
        ("iyakuKoumokuSelectSwitchRadio", "2"),
        ("koumoku1Value", ""), ("koumoku1Word", ""), ("koumoku1HowtoSearch", "and"),
        ("koumoku2Value", ""), ("koumoku2Word", ""), ("koumoku2HowtoSearch", "and"),
        ("koumoku3Value", ""), ("koumoku3Word", ""), ("koumoku3HowtoSearch", "and"),
        ("howtoRdSearchSel", "or"),
        ("relationDoc1Sel", ""), ("relationDoc1Word", ""), ("relationDoc1HowtoSearch", "and"), ("relationDoc1FrDt", ""), ("relationDoc1ToDt", ""),
        ("relationDocHowtoSearchBetween12", "and"),
        ("relationDoc2Sel", ""), ("relationDoc2Word", ""), ("relationDoc2HowtoSearch", "and"), ("relationDoc2FrDt", ""), ("relationDoc2ToDt", ""),
        ("relationDocHowtoSearchBetween23", "and"),
        ("relationDoc3Sel", ""), ("relationDoc3Word", ""), ("relationDoc3HowtoSearch", "and"), ("relationDoc3FrDt", ""), ("relationDoc3ToDt", ""),
        ("isNewReleaseDisp", "true"),
        ("listCategory", "BOOK" if page else ""),
        ("personFlg", "false"), ("gs1code", ""),
    ]
    if page:
        f += [("pages", str(page)), ("searchCnt", ""), ("totalPages", "")]
    else:
        f += [("btnA.x", "10"), ("btnA.y", "10")]   # 「検索」ボタン(画像)を押した扱い
    return f


# ---------------------------------------------------------------- HTTP
class Session:
    def __init__(self) -> None:
        self.cj = http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cj))
        self.op.addheaders = [("User-Agent", UA)]
        self.requests = 0

    def get(self, url: str) -> str:
        return self._do(urllib.request.Request(url))

    def post(self, url: str, fields: list[tuple[str, str]], ajax: bool = False) -> str:
        h = {"Content-Type": "application/x-www-form-urlencoded", "Referer": FORM_URL}
        if ajax:
            h.update({"X-Requested-With": "XMLHttpRequest", "Accept": "application/json"})
        body = urllib.parse.urlencode(fields).encode("utf-8")
        return self._do(urllib.request.Request(url, data=body, headers=h))

    def _do(self, req: urllib.request.Request, retries: int = 3) -> str:
        last = None
        for i in range(retries):
            try:
                self.requests += 1
                with self.op.open(req, timeout=120) as r:
                    out = r.read().decode("utf-8", "replace")
                time.sleep(SLEEP)
                return out
            except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as e:
                last = e
                time.sleep(5 * (i + 1))
        raise RuntimeError(f"request failed: {req.full_url}: {last}")


# ---------------------------------------------------------------- 解析
ROW_RE = re.compile(r"<tr class='TrColor0\d'>(.*?)</tr>", re.S)
TD_RE = re.compile(r"<td>(.*?)</td>", re.S)
A_RE = re.compile(r"<a[^>]*href='([^']*)'[^>]*>(.*?)</a>", re.S)
COUNT_RE = re.compile(r"検索結果(\d+)件／全(\d+)ページ")
PDF_CODE_RE = re.compile(r"ResultDataSetPDF/([0-9A-Za-z_]+)")     # 添付文書PDFのコード(企業コード_packins番号)
PDF_DATE_RE = re.compile(r"\((\d{4})年(\d{2})月(\d{2})日\)")      # リンク文字「PDF(2022年04月01日)」の日付
# 上限超えのとき: <strong>検索結果<span …>1160</span>件見つかりました。<BR><BR>検索結果の上限は1000件です。…</strong>
OVER_RE = re.compile(r"検索結果(?:\s|<[^>]+>)*(\d+)(?:\s|<[^>]+>)*件見つかりました")


def clean(s: str) -> str:
    s = re.sub(r"</div>\s*<div>", " / ", s, flags=re.I)
    s = re.sub(r"<br\s*/?>", " / ", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


def parse_effect_options(form_html: str) -> list[tuple[str, str]]:
    """薬効分類プルダウンの (コード, 名前) 一覧。コードは2桁(大分類)と3桁(小分類)"""
    m = re.search(r'<select[^>]*name="effectValue".*?</select>', form_html, re.S)
    if not m:
        raise RuntimeError("薬効分類のプルダウンが見つかりません(PMDAのページ構成が変わった?)")
    opts = re.findall(r'<option[^>]*value="([^"]*)"[^>]*>(.*?)</option>', m.group(0), re.S)
    out = []
    for code, name in opts:
        code = code.strip()
        if not code:
            continue
        out.append((code, clean(name).lstrip("-").strip()))
    return out


def parse_rows(fragment: str, doc: str = "if") -> list[dict]:
    items = []
    for row in ROW_RE.findall(fragment):
        tds = TD_RE.findall(row)
        if len(tds) < 4:
            continue
        g_td, n_td, c_td, if_td = tds[0], tds[1], tds[2], tds[3]
        m = re.search(r"GeneralList/([0-9A-Za-z]+)", g_td)
        detail = m.group(1) if m else ""
        ifs = []
        for href, title in A_RE.findall(if_td):
            href = html.unescape(href.strip())
            if not href:
                continue
            if doc == "tenpu":
                # 添付文書列は PDF / HTML(javascript) / XML の3リンク。PDFのコードだけ取れば他は組み立てられる
                mc = PDF_CODE_RE.search(href)
                if not mc:
                    continue
                md = PDF_DATE_RE.search(title)
                ifs.append({"u": mc.group(1), "t": "-".join(md.groups()) if md else ""})
                continue
            u = href[len(IF_BASE):] if href.startswith(IF_BASE) else href
            if u.endswith(".pdf") and not u.startswith("http"):
                u = u[:-4]
            ifs.append({"u": u, "t": clean(title)})
        if not ifs:
            continue
        items.append({
            "g": clean(g_td), "n": clean(n_td), "c": clean(c_td), "d": detail, "f": ifs,
        })
    return items


# ---------------------------------------------------------------- 収集
def fetch_category(s: Session, code: str, name: str, date_from: str = "", date_to: str = "",
                   doc: str = "if") -> tuple[list[dict] | None, int]:
    """1つの薬効分類(必要なら更新年月日の範囲つき)を全ページ取る。上限超え(1000件超)のときは (None, 件数) を返す"""
    label = f"{code} {name}" + (f" [{date_from}〜{date_to}]" if date_from or date_to else "")
    page1 = s.post(SEARCH_URL, search_fields(effect=code, date_from=date_from, date_to=date_to, doc=doc))
    over = OVER_RE.search(page1)
    if over or "検索結果の上限は" in page1:
        return None, int(over.group(1)) if over else LIMIT + 1
    m = COUNT_RE.search(page1)
    if not m:
        if "条件に該当する添付文書はありませんでした" in page1:
            log(f"  {label}: 0件")
            return [], 0
        # 0件でも上限超えでもないのに件数が読めない = 想定外(取りこぼし防止のため止める)
        raise RuntimeError(f"件数が読み取れません({label})。PMDAのページ構成が変わった?")
    total, pages = int(m.group(1)), int(m.group(2))
    items = parse_rows(page1, doc)
    for p in range(2, pages + 1):
        raw = s.post(PAGE_URL.format(page=p), search_fields(effect=code, page=p, date_from=date_from, date_to=date_to, doc=doc), ajax=True)
        try:
            j = json.loads(raw)
        except json.JSONDecodeError as e:
            raise RuntimeError(f"ページ送りの応答がJSONではありません({code} p{p}): {raw[:200]}") from e
        items += parse_rows(j.get("ResultList", ""), doc)
    log(f"  {label}: {total}件/{pages}ページ → {len(items)}行({DOC_LABELS[doc]}あり)")
    return items, total


def fetch_category_split_by_date(s: Session, code: str, name: str, warnings: list[str],
                                 date_from: str = "19000101", date_to: str | None = None,
                                 doc: str = "if") -> tuple[list[dict], int]:
    """小分類でも1000件を超えるとき: 添付文書の更新年月日の範囲を半分ずつに割って取る(再帰)"""
    if date_to is None:
        date_to = f"{datetime.now(JST).year + 1}1231"
    items, total = fetch_category(s, code, name, date_from, date_to, doc)
    if items is not None:
        return items, total
    if date_from >= date_to:
        warnings.append(f"{code} {name} [{date_from}]: 1日で{total}件・上限超え(未取得)")
        return [], 0
    d0 = datetime.strptime(date_from, "%Y%m%d")
    d1 = datetime.strptime(date_to, "%Y%m%d")
    mid = d0 + (d1 - d0) / 2
    mid_s = mid.strftime("%Y%m%d")
    next_s = (mid + timedelta(days=1)).strftime("%Y%m%d")
    a, ta = fetch_category_split_by_date(s, code, name, warnings, date_from, mid_s, doc)
    b, tb = fetch_category_split_by_date(s, code, name, warnings, next_s, date_to, doc)
    return a + b, ta + tb


def collect(s: Session, doc: str = "if") -> tuple[list[dict], dict]:
    form_html = s.get(FORM_URL)
    opts = parse_effect_options(form_html)
    names = dict(opts)
    parents = [c for c, _ in opts if len(c) == 2]
    children = {p: [c for c, _ in opts if len(c) == 3 and c.startswith(p)] for p in parents}
    log(f"薬効分類: 大分類{len(parents)} / 小分類{sum(len(v) for v in children.values())}")

    all_items: list[dict] = []
    warnings: list[str] = []
    expected = 0
    for p in parents:
        items, total = fetch_category(s, p, names[p], doc=doc)
        if items is not None:
            for it in items:
                it["e"] = p
            all_items += items
            expected += total
            continue
        log(f"  {p} {names[p]}: {total}件で上限超え → 小分類に分けて取得")
        for c in (children[p] or [p]):
            items2, total2 = fetch_category(s, c, names[c], doc=doc)
            if items2 is None:
                log(f"  {c} {names[c]}: {total2}件で上限超え → 更新年月日で分けて取得")
                items2, total2 = fetch_category_split_by_date(s, c, names[c], warnings, doc=doc)
            for it in items2:
                it["e"] = c
            all_items += items2
            expected += total2

    # 重複除去(同じ行が複数分類に出ることはほぼ無いはずだが念のため)
    seen = set()
    uniq = []
    for it in all_items:
        key = (it["d"], it["n"], tuple(x["u"] for x in it["f"]))
        if key in seen:
            continue
        seen.add(key)
        uniq.append(it)
    uniq.sort(key=lambda x: (x["g"], x["n"]))
    meta = {
        "fetched_at": datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S"),
        "count": len(uniq),
        "rows_expected": expected,
        "requests": s.requests,
        "categories": {c: n for c, n in opts},
        "warnings": warnings,
    }
    return uniq, meta


def refresh(root: Path, doc: str = "if") -> dict:
    out = root / "data" / OUT_NAMES[doc]
    out.parent.mkdir(parents=True, exist_ok=True)
    s = Session()
    log(f"{DOC_LABELS[doc]}一覧の取得を開始")
    items, meta = collect(s, doc)
    data = {"meta": meta, "items": items}
    out.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8", newline="\n")
    log(f"保存: {out} ({meta['count']}件, 要求{meta['requests']}回)")
    for w in meta["warnings"]:
        log(f"!! {w}")
    return meta


if __name__ == "__main__":
    argv = sys.argv[1:]
    doc_arg = "tenpu" if "--tenpu" in argv else "if"
    paths = [a for a in argv if not a.startswith("--")]
    root = Path(paths[0]) if paths else Path(__file__).resolve().parent.parent
    refresh(root, doc_arg)
