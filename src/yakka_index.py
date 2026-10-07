# -*- coding: utf-8 -*-
"""
添付文書検索に「規格ごとの薬価」を載せるためのデータを作る。2つのデータをサイト生成時に突き合わせる:

  1) 薬価リスト … data/yakka_index.json(書き手=Actions。毎日0:15の if-index に相乗り)
     厚生労働省「薬価基準収載品目リスト」のページ → Excel 4つ(内用薬・注射薬・外用薬・歯科用薬剤)
     → {薬価基準収載医薬品コード: [品名, 規格, 薬価, 経過措置の期限, 統一名収載なら1]}
     毎日ページを1回だけ見て、Excelのファイル名(=中身の版)が変わったときだけ取り直す(厚労省へは1日1回+更新時に4ファイル)
  2) 規格の対応表 … data/yj_index.json(書き手=自宅PC。毎日の自動更新 run.py --shikibetsu のついで)
     添付文書1文書 → 載っている製品(販売名)ごとのYJコード。PC内のXMLの写し(xml_cache.py)から読むだけ=PMDAへのアクセスは増えない
  → build_site.py が attach() で、添付文書検索の候補ごとに「販売名・規格・薬価」の行を付ける

突き合わせ方:
  - YJコード(12桁)が薬価リストのコードと同じならその薬価(先発品や銘柄別収載の後発品はこれで当たる)
  - 当たらなければ、頭9桁(成分・剤形・規格)が同じ「統一名収載」の行(メーカー名なしの一般名の行)の薬価を使う
    (安い後発品は一般名でまとめて収載されるので、製品ごとのYJコードがリストに無い)
  - それでも無いもの(保険適用外の消毒薬・販売中止で削除済み・収載前など)は「掲載なし」

使い方: python src/yakka_index.py         (薬価リストを厚労省から確認・更新。変わっていなければ何もしない)
        python src/yakka_index.py --force (変わっていなくても取り直す)
        python src/yakka_index.py --yj    (規格の対応表をPC内のXMLの写しから更新。自宅PC専用)
        python src/run.py --build-only --yakka-index   (薬価リストの更新 + サイト生成。Actionsと同じ)
"""
from __future__ import annotations

import html
import io
import json
import re
import sys
import time
import unicodedata
import urllib.parse
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pmda_watch  # noqa: E402  (HTTP取得・XMLの名前空間)
import shikibetsu_index  # noqa: E402  (対象文書の洗い出し・日本語テキストの取り出しを流用)
import xml_cache  # noqa: E402  (XMLのPC内の写し)

JST = timezone(timedelta(hours=9))
NS = pmda_watch.NS
OUT_NAME = "yakka_index.json"
YJ_NAME = "yj_index.json"
DATA_VERSION = 1            # 薬価リストの記録の形式。変えると次回は必ず取り直す
YJ_VERSION = 1              # 規格の対応表の形式。変えると全文書を写しから読み直す(数分)
REFETCH_DAYS = 28           # ファイル名が同じでも、この日数たったら念のため取り直す(差し替え対策)
SLEEP = 1.0                 # 厚労省サイトへの待ち(秒)
MIN_ROWS = 8000             # 4ファイル合計がこれより少なければ、読み取り失敗とみなして古いリストを残す

# 年度ごとのページ(毎年4月の薬価改定で新しいページができる。年度の途中の収載・改定は同じページの中で差し替わる)
PAGE_URL = "https://www.mhlw.go.jp/topics/{fy}/04/tp{fy}0401-01.html"
KINDS = {"01": "内用薬", "02": "注射薬", "03": "外用薬", "04": "歯科用薬剤"}

_X = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_CODE = re.compile(r"^[0-9A-Z]{12}$")


def log(msg: str) -> None:
    print(f"[{datetime.now(JST).strftime('%H:%M:%S')}] {msg}", flush=True)


def _nfkc(s) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", str(s or ""))).strip()


# ---------------------------------------------------------------- Excel(.xlsx)を標準ライブラリだけで読む
def _col(ref: str) -> int:
    n = 0
    for ch in re.match(r"[A-Z]+", ref).group(0):
        n = n * 26 + ord(ch) - 64
    return n - 1


def xlsx_rows(data: bytes) -> list[list]:
    """1枚目のシートを行のリストで返す(セルの値は文字列。空は None)。ふりがな(rPh)は読まない"""
    z = zipfile.ZipFile(io.BytesIO(data))
    ss: list[str] = []
    if "xl/sharedStrings.xml" in z.namelist():
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).iter(_X + "si"):
            parts = []
            for ch in si:
                if ch.tag == _X + "t":
                    parts.append(ch.text or "")
                elif ch.tag == _X + "r":
                    t = ch.find(_X + "t")
                    parts.append((t.text or "") if t is not None else "")
            ss.append("".join(parts))
    rid = ET.fromstring(z.read("xl/workbook.xml")).find(f"{_X}sheets/{_X}sheet").get(_R + "id")
    target = next(r.get("Target") for r in ET.fromstring(z.read("xl/_rels/workbook.xml.rels")) if r.get("Id") == rid)
    path = target.lstrip("/") if target.startswith("/") else "xl/" + target
    rows: list[list] = []
    for row in ET.fromstring(z.read(path)).iter(_X + "row"):
        cells: dict[int, str | None] = {}
        for i, c in enumerate(row.iter(_X + "c")):
            j = _col(c.get("r")) if c.get("r") else i
            t = c.get("t")
            v = c.find(_X + "v")
            if t == "s" and v is not None:
                cells[j] = ss[int(v.text)]
            elif t == "inlineStr":
                cells[j] = "".join(x.text or "" for x in c.iter(_X + "t"))
            else:
                cells[j] = v.text if v is not None else None
        rows.append([cells.get(j) for j in range(max(cells) + 1)] if cells else [])
    return rows


def parse_list(data: bytes, kind: str) -> dict[str, list]:
    """薬価基準収載品目リスト(Excel1つ)→ {コード: [品名, 規格, 薬価, 経過措置の期限, 統一名収載なら1]}
    列は見出しの名前で探す(列の並びが変わっても読めるように)"""
    rows = xlsx_rows(data)
    hi = next((i for i, r in enumerate(rows[:20]) if any("薬価基準収載医薬品コード" in _nfkc(c) for c in r)), None)
    if hi is None:
        raise RuntimeError(f"{kind}: 見出し行(薬価基準収載医薬品コード)が見つかりません(Excelの形が変わった?)")
    heads = [re.sub(r"\s+", "", _nfkc(c)) for c in rows[hi]]

    def find(pred) -> int | None:
        return next((i for i, h in enumerate(heads) if h and pred(h)), None)

    col = {
        "code": find(lambda h: "薬価基準収載医薬品コード" in h),
        "name": find(lambda h: h.startswith("品名")),
        "kikaku": find(lambda h: h.startswith("規格")),
        "maker": find(lambda h: h.startswith("メーカー")),
        "price": find(lambda h: h.startswith("薬価") and "コード" not in h and "基準" not in h),
        "keika": find(lambda h: h.startswith("経過措置")),
    }
    for k in ("code", "kikaku", "price"):
        if col[k] is None:
            raise RuntimeError(f"{kind}: 「{k}」の列が見つかりません(見出し: {heads})")

    def cell(r: list, k: str):
        i = col[k]
        return r[i] if i is not None and i < len(r) else None

    out: dict[str, list] = {}
    for r in rows[hi + 1:]:
        code = _nfkc(cell(r, "code")).upper()
        if not _CODE.match(code):
            continue   # 見出しの繰り返し・空行
        try:
            price = round(float(str(cell(r, "price")).replace(",", "")), 2)
        except ValueError:
            continue
        out[code] = [_nfkc(cell(r, "name")), _nfkc(cell(r, "kikaku")), price, _nfkc(cell(r, "keika")),
                     0 if _nfkc(cell(r, "maker")) else 1]
    return out


# ---------------------------------------------------------------- 厚労省のページからExcelの場所を探す
def _get_text(url: str) -> str:
    raw = pmda_watch.http_get(url)
    m = re.search(rb'charset=["\']?([\w-]+)', raw[:4000], re.I)
    enc = m.group(1).decode("ascii", "ignore") if m else "utf-8"
    try:
        return raw.decode(enc)
    except (LookupError, UnicodeDecodeError):
        return raw.decode("cp932", errors="replace")


def find_page() -> tuple[str, str, dict[str, str]]:
    """今年度のページ(無ければ前年度)を開いて (URL, 適用日の表記, {"01": Excelの絶対URL, …}) を返す。
    年度は4月始まり(3月まではまだ前年度のページの薬価が有効なので、新年度のページが先に出ていても使わない)"""
    now = datetime.now(JST)
    fy = now.year if now.month >= 4 else now.year - 1
    tried = []
    for y in (fy, fy - 1):
        url = PAGE_URL.format(fy=y)
        try:
            page = _get_text(url)
        except pmda_watch.NotFound:
            tried.append(f"{url}(404)")
            continue
        links: dict[str, str] = {}
        for m in re.finditer(r'href="([^"]+?_(0[1-4])\.xlsx)"', page):
            links.setdefault(m.group(2), urllib.parse.urljoin(url, html.unescape(m.group(1))))   # 目次(先頭)のものを採用
        title = re.search(r"<title>(.*?)</title>", page, re.S)
        title = _nfkc(re.sub(r"<[^>]+>", "", html.unescape(title.group(1)))) if title else ""
        m = re.search(r"[（(]([^（()）]*適用)[)）]", title)
        if len(links) == 4:
            return url, (m.group(1) if m else ""), links
        tried.append(f"{url}(Excelのリンク{len(links)}/4件)")
        time.sleep(SLEEP)
    raise RuntimeError("薬価基準収載品目リストのページが見つかりません: " + " / ".join(tried))


def _file_date(u: str) -> str:
    m = re.search(r"tp(\d{4})(\d{2})(\d{2})-", u)
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else ""


def load(root: Path, name: str = OUT_NAME) -> dict | None:
    f = Path(root) / "data" / name
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        log(f"!! data/{name} を読めませんでした({e})")
        return None


def _write(f: Path, obj: dict) -> None:
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp.json")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8", newline="\n")
    tmp.replace(f)


def refresh(root: Path, force: bool = False) -> dict:
    """厚労省のページを見て、Excelが変わっていれば取り直して data/yakka_index.json を書く。meta を返す。
    読み取りに失敗したら例外(古いリストはそのまま残る。部分的なリストで上書きしない)"""
    root = Path(root)
    out = root / "data" / OUT_NAME
    old = load(root) or {}
    om = old.get("meta") or {}
    page_url, applied, links = find_page()
    same = (old.get("p") and om.get("v") == DATA_VERSION
            and [f.get("url") for f in om.get("files") or []] == [links[k] for k in KINDS])
    try:
        age = datetime.now(JST).replace(tzinfo=None) - datetime.strptime(om.get("fetched_at") or "", "%Y-%m-%d %H:%M:%S")
    except ValueError:
        age = timedelta(days=999)
    if same and not force and age < timedelta(days=REFETCH_DAYS):
        if om.get("applied") != applied or om.get("page") != page_url:
            om.update({"applied": applied, "page": page_url})   # ページの表記(適用日)だけ変わった
            _write(out, {"meta": om, "p": old["p"]})
            log(f"薬価リスト: Excelは変わらず・適用日の表記だけ更新({applied})")
        else:
            log(f"薬価リスト: 変更なし({applied}・{om.get('count', 0):,}品目)")
        return om

    prices: dict[str, list] = {}
    files = []
    for k, kind in KINDS.items():
        time.sleep(SLEEP)
        rows = parse_list(pmda_watch.http_get(links[k]), kind)
        if not rows:
            raise RuntimeError(f"{kind}: 品目が0件(Excelの形が変わった?): {links[k]}")
        prices.update(rows)
        files.append({"kind": kind, "url": links[k], "date": _file_date(links[k]), "rows": len(rows)})
        log(f"{kind}: {len(rows):,}品目 ({links[k].rsplit('/', 1)[-1]})")
    n_old = len(old.get("p") or {})
    if len(prices) < MIN_ROWS or (n_old and len(prices) < n_old * 0.8):
        raise RuntimeError(f"品目数が少なすぎます({len(prices):,}件。前回{n_old:,}件)。読み取り失敗とみなして古いリストを残します")
    meta = {
        "v": DATA_VERSION,
        "fetched_at": datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S"),
        "page": page_url,
        "applied": applied,
        "files": files,
        "count": len(prices),
        "unified": sum(1 for r in prices.values() if r[4]),
        "cols": ["品名", "規格", "薬価", "経過措置による使用期限", "統一名収載(メーカー名なし)なら1"],
    }
    _write(out, {"meta": meta, "p": dict(sorted(prices.items()))})
    log(f"data/{OUT_NAME}: {len(prices):,}品目({applied}・前回{n_old:,}品目)")
    return meta


# ---------------------------------------------------------------- 規格の対応表(添付文書XML → 販売名ごとのYJコード)
def extract_products(xml_text: str) -> list[list[str]]:
    """添付文書XMLの「承認等」欄から [[販売名, YJコード], …] を載っている順に返す"""
    parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True, insert_pis=True))
    root = ET.fromstring(xml_text, parser=parser)
    out: list[list[str]] = []
    for b in root.iter(NS + "DetailBrandName"):
        if not b.get("id"):
            continue
        names: list[str] = []
        for a in b.iter(NS + "ApprovalBrandName"):
            names += shikibetsu_index._ja_texts(a)
        name = re.sub(r"\s*\n\s*", " ", names[0]).strip() if names else ""
        yj = next(((y.text or "").strip().upper() for y in b.iter(NS + "YJCode")), "")
        if (name or yj) and [name, yj] not in out:
            out.append([name, yj])
    return out


def refresh_yj(root: Path) -> dict:
    """data/yj_index.json を差分更新する(新しい文書・改版された文書だけ)。PC内のXMLの写しから読むだけでPMDAへは行かない。
    写しがまだ無い文書は今回は飛ばす(識別コード・注意チェックの取得で写しができたら次回拾う)。中身が変わったときだけ書く"""
    root = Path(root)
    tenpu = load(root, "tenpu_index.json")
    if not tenpu:
        raise RuntimeError("data/tenpu_index.json がありません")
    targets = shikibetsu_index.list_targets(tenpu)
    data = load(root, YJ_NAME) or {}
    docs: dict = data.get("docs") or {}
    changed = False
    if len(targets) > 3000:   # 一覧の取得失敗で対象が激減したときに消しすぎない
        for u in [u for u in docs if u not in targets]:
            del docs[u]
            changed = True
    done = no_cache = errors = 0
    for u, t in targets.items():
        r = docs.get(u)
        if r and r.get("t") == t and r.get("v") == YJ_VERSION:
            continue
        x = xml_cache.get(u)
        if not x:
            no_cache += 1   # 改版前の記録があればそれを使い続ける
            continue
        rec: dict = {"t": t, "v": YJ_VERSION}
        try:
            rec["b"] = extract_products(x)
        except Exception as e:  # noqa: BLE001
            rec["b"] = []
            rec["err"] = f"{type(e).__name__}: {e}"
            errors += 1
        docs[u] = rec
        done += 1
        changed = True
    meta = {
        "targets": len(targets),
        "docs": sum(1 for u in targets if u in docs),
        "pending": sum(1 for u, t in targets.items() if (docs.get(u) or {}).get("t") != t),
        "products": sum(len(r.get("b") or []) for r in docs.values()),
        "with_yj": sum(1 for r in docs.values() for _n, y in r.get("b") or [] if y),
        "errors": sum(1 for r in docs.values() if r.get("err")),
    }
    om = {k: v for k, v in (data.get("meta") or {}).items() if k != "updated_at"}
    if changed or om != meta:
        meta = {"updated_at": datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S"), **meta}
        _write(root / "data" / YJ_NAME, {"meta": meta, "docs": dict(sorted(docs.items()))})
    else:
        meta = data.get("meta") or meta
    log(f"規格の対応表: 今回{done}文書を写しから読み込み(写し待ち{no_cache}件・エラー{errors}件) / "
        f"全体 {meta['docs']}/{meta['targets']}文書・{meta['products']}製品(YJコードあり{meta['with_yj']})")
    return meta


# ---------------------------------------------------------------- サイト生成時の突き合わせ
def attach(items: list[dict], yj: dict | None, yakka: dict | None) -> tuple[list[dict], dict]:
    """添付文書一覧の各項目に "p"(規格ごとの薬価)を付けたコピーを返す。
    "p" = [[販売名, 規格, 薬価, 経過措置の期限(あれば)], …]。薬価が無い製品は [販売名] だけ。
    製品が1つだけの文書は販売名を省く(候補の見出しと同じなので)。対応表に無い文書(写し待ち)には "p" を付けない"""
    docs = (yj or {}).get("docs") or {}
    prices = (yakka or {}).get("p") or {}
    stats = {"products": 0, "priced": 0}
    if not docs or not prices:
        return items, stats
    unified: dict[str, list[str]] = {}
    for code, r in prices.items():
        if r[4]:
            unified.setdefault(code[:9], []).append(code)

    def price_of(code: str) -> list | None:
        r = prices.get(code)
        if r:
            return r
        cands = unified.get(code[:9]) if len(code) == 12 else None
        if cands and len({prices[c][2] for c in cands}) == 1:   # 統一名の行が複数で値段が違うときは決めつけない
            return prices[cands[0]]
        return None

    out = []
    for it in items:
        prods: list[list[str]] = []
        known = False
        for fx in it.get("f") or []:
            rec = docs.get(fx.get("u") or "")
            if rec is None:
                continue
            known = True
            for p in rec.get("b") or []:
                if p not in prods:
                    prods.append(p)
        if not known:
            out.append(it)
            continue
        rows = []
        for name, code in prods:
            r = price_of(code) if code else None
            nm = name if len(prods) > 1 else ""
            rows.append([nm, r[1], r[2]] + ([r[3]] if r[3] else []) if r else [nm])
            stats["products"] += 1
            stats["priced"] += 1 if r else 0
        out.append({**it, "p": rows})
    return out, stats


def summary(yakka: dict | None) -> str:
    """ページの注記用: 「令和8年10月1日適用・12,428品目」"""
    m = (yakka or {}).get("meta") or {}
    if not m:
        return ""
    return "・".join(x for x in (m.get("applied") or "", f"{m.get('count', 0):,}品目") if x)


if __name__ == "__main__":
    argv = sys.argv[1:]
    here = Path(__file__).resolve().parent.parent
    if "--yj" in argv:
        refresh_yj(here)
    else:
        refresh(here, force="--force" in argv)
