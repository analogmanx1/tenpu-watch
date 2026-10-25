# -*- coding: utf-8 -*-
"""
薬理作用検索の元データを作る。添付文書XMLの「18. 薬効薬理」の章(18.1 作用機序・18.2 以降の薬理作用など)を、
見出し・本文・表の並びのまま抜き出して data/yakuri_index.json に溜める。

- 読むのは **PC内のXMLの写し(xml_cache.py)だけ**。PMDAへはアクセスしない(写しは識別コード・注意チェックの取得で毎日そろう)
  → 毎日の自動更新(run.py --shikibetsu)のついでに、新しい文書・改版された文書だけ読み直す。写しがまだ無い文書は次回拾う
- 書き手は自宅PCだけ(衝突時は ours。scripts/local_update.ps1 の Resolve-StuckMerge に登録済み)
- 中身がまったく同じ添付文書(後発品どうしなど)は1つにまとめて持つ("docs" は文書→中身の番号、"bodies" が中身)
- インタビューフォーム(IF)の本文は載せない(各製薬企業の著作物で、PMDAが無断の転載をお断りしているため。2026-10-25 アナログマンさんと相談して決定)。
  サイトでは各製品のIF(PDF)へのリンクだけ添える

添付文書の書き方(新記載要領のXML):
  <EfficacyPharmacology> の直下に <MechanismOfAction>(18.1 作用機序)・<OtherInformation>(18.2 以降。見出しは <Header>)・
  <MeasurementMethod>(放射性医薬品などの「測定法」)が並ぶ。中は <OrderedList> の <Item>(見出し+<Detail>)・表(<TblBlock>)・図(<Graphic>)
  文献番号(<ReferenceBookRef>)は落とす。図は文字にできないので「図: 題名」だけ残す

使い方: python src/yakuri_index.py           (写しから差分更新。初回・DATA_VERSION を上げた直後は全文書で数分)
        python src/run.py --shikibetsu         (毎日の自動実行と同じ: 識別コード・注意チェックのついでに更新)
"""
from __future__ import annotations

import json
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pmda_watch  # noqa: E402  (XMLの名前空間)
import shikibetsu_index  # noqa: E402  (対象文書の洗い出しを流用)
import xml_cache  # noqa: E402  (XMLのPC内の写し)
from fukusayo_index import JST, log, load, _local, _NOTE, _hash, _write  # noqa: E402  (副作用検索と同じ道具)

NS = pmda_watch.NS
OUT_NAME = "yakuri_index.json"
DATA_VERSION = 2            # 抜き出し方・記録の形式。変えたら上げる(全文書を写しから読み直す=数分)
SEC_NO = "18"               # 新記載要領で「薬効薬理」の章番号は18で固定
TOP_TITLES = {"MechanismOfAction": "作用機序", "MeasurementMethod": "測定法"}
_XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"
_SKIP = {"ReferenceBookRef", "CommentRef", "HeaderRef"}   # 文献番号・表の注の印・「［11.1参照］」の参照先(番号が取れないので落とす)


def _inline(el, brands: dict[str, str]) -> str:
    """要素内の文字を1行にする。文献番号・注の印だけの <Sup>・見出しの参照は落とし、販売名の参照は販売名に置き換える"""
    out: list[str] = []

    def rec(e, top=False):
        if isinstance(e.tag, str):
            t = _local(e.tag)
            if t in _SKIP or (t == "Sup" and _NOTE.match("".join(e.itertext()))):
                pass
            elif t == "ApprovalBrandNameRef":
                out.append(brands.get(e.get("ref") or "", "本剤"))
            elif t == "Lang" and e.get(_XML_LANG) not in (None, "ja"):
                pass
            else:
                if e.text:
                    out.append(e.text)
                for c in e:
                    rec(c)
        if e.tail and not top:
            out.append(e.tail)

    rec(el, top=True)
    s = re.sub(r"\s+", " ", "".join(out)).strip()
    s = re.sub(r"(?<=[^\x00-\x7f]) (?=[^\x00-\x7f])", "", s)   # XMLの折り返しで日本語の間に入った空白
    s = re.sub(r"\s*[,、]+\s*(?=[。．）)])", "", s)          # 文献番号を落とした跡の「,」
    return s.strip(" ,，")


def _header(el, brands: dict[str, str]) -> str:
    hd = el.find(NS + "Header")
    return _inline(hd, brands) if hd is not None else ""


def _table(el, brands: dict[str, str]) -> list:
    """表 → ["t", 題名, [[セル, …], …], 注]"""
    cap = " ".join(_inline(c, brands) for c in el.iter(NS + "TblCaption"))
    rows = [[_inline(c, brands) for c in r.iter(NS + "SimpTblCell")] for r in el.iter(NS + "SimpTblRow")]
    foot = " ".join([_inline(c, brands) for c in el.iter(NS + "SimpTblFoot")] + [_inline(c, brands) for c in el.iter(NS + "Comment")])
    return ["t", cap, [r for r in rows if any(r)], foot.strip()]


def _walk(el, prefix: str, lv: int, out: list, brands: dict[str, str]) -> None:
    """見出し ["h", 深さ, "18.2.1 見出し"]・本文 ["p", 文]・箇条書き ["l", 文]・表 ["t", …]・図 ["g", 題名]・注 ["n", 文] を out に足す"""
    for ch in el:
        t = _local(ch.tag)
        if not t or t == "Header":
            continue
        if t == "OrderedList":
            k = 0
            for item in ch.findall(NS + "Item"):
                hd = _header(item, brands)
                if hd:
                    k += 1
                    num = f"{prefix}.{k}"
                    out.append(["h", min(lv, 3), f"{num} {hd}"])
                    _walk(item, num, lv + 1, out, brands)
                else:
                    _walk(item, prefix, lv, out, brands)
        elif t in ("SimpleList", "UnorderedList"):
            for item in ch.findall(NS + "Item"):
                s = _inline(item, brands)
                if s:
                    out.append(["l", s])
        elif t in ("TblBlock", "SimpleTable"):
            tb = _table(ch, brands)
            if tb[2]:
                out.append(tb)
        elif t == "Graphic":
            cap = " ".join(_inline(c, brands) for c in ch.iter(NS + "GraphicCaption")) or _inline(ch, brands)
            out.append(["g", cap])
        elif t == "Comment":
            s = _inline(ch, brands)
            if s:
                out.append(["n", s])
        elif t == "Lang":
            s = _inline(ch, brands)
            if s:
                out.append(["p", s])
        else:
            _walk(ch, prefix, lv, out, brands)


def extract(xml_text: str) -> dict | None:
    """1文書の「18. 薬効薬理」 → {"b": [ブロック, …]}(ブロックの形は _walk)。章が無い文書は None"""
    root = ET.fromstring(xml_text)
    ep = next((e for e in root.iter(NS + "EfficacyPharmacology")), None)
    if ep is None:
        return None
    brands: dict[str, str] = {}
    for d in root.iter(NS + "DetailBrandName"):
        a = d.find(NS + "ApprovalBrandName")
        if d.get("id") and a is not None:
            brands[d.get("id")] = _inline(a, {})
    out: list = []
    n = 0
    for ch in ep:
        t = _local(ch.tag)
        if not t or t == "Header":
            continue
        n += 1
        num = f"{SEC_NO}.{n}"
        out.append(["h", 1, f"{num} {TOP_TITLES.get(t) or _header(ch, brands) or 'その他'}"])
        _walk(ch, num, 2, out, brands)
    return {"b": out}


def refresh(root: Path) -> dict:
    """data/yakuri_index.json を差分更新する(新しい文書・改版された文書だけ)。PC内のXMLの写しから読むだけ。中身が変わったときだけ書く
    docs = {PDFコード: [更新日, 中身の番号("" = 薬効薬理の章なし), 形式の版]} / bodies = {中身の番号: extract() の結果}"""
    root = Path(root)
    tf = root / "data" / "tenpu_index.json"
    if not tf.exists():
        raise RuntimeError("data/tenpu_index.json がありません")
    targets = shikibetsu_index.list_targets(json.loads(tf.read_text(encoding="utf-8")))
    data = load(root, OUT_NAME) or {}
    docs: dict = data.get("docs") or {}
    bodies: dict = data.get("bodies") or {}
    changed = False
    if len(targets) > 3000:   # 一覧の取得失敗で対象が激減したときに消しすぎない
        for u in [u for u in docs if u not in targets]:
            del docs[u]
            changed = True
    done = no_cache = errors = 0
    for u, t in targets.items():
        r = docs.get(u)
        if r and r[0] == t and r[2] == DATA_VERSION:
            continue
        x = xml_cache.get(u)
        if not x:
            no_cache += 1   # 改版前の記録があればそれを使い続ける
            continue
        try:
            body = extract(x)
        except Exception as e:  # noqa: BLE001
            log(f"  !! {u}: {type(e).__name__}: {e}")
            errors += 1
            continue
        h = ""
        if body is not None:
            h = _hash(body)
            bodies[h] = body
        docs[u] = [t, h, DATA_VERSION]
        done += 1
        changed = True
    used = {r[1] for r in docs.values() if r[1]}
    for h in [h for h in bodies if h not in used]:
        del bodies[h]
        changed = True
    meta = {
        "targets": len(targets),
        "docs": sum(1 for u in targets if u in docs),
        "pending": sum(1 for u, t in targets.items() if (docs.get(u) or [""])[0] != t),
        "with_ep": sum(1 for r in docs.values() if r[1]),
        "bodies": len(bodies),
    }
    om = {k: v for k, v in (data.get("meta") or {}).items() if k != "updated_at"}
    if changed or om != meta:
        meta = {"updated_at": datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S"), **meta}
        _write(root / "data" / OUT_NAME, {"meta": meta, "docs": dict(sorted(docs.items())), "bodies": dict(sorted(bodies.items()))})
    else:
        meta = data.get("meta") or meta
    log(f"薬理作用: 今回{done}文書を写しから読み込み(写し待ち{no_cache}件・エラー{errors}件) / "
        f"全体 {meta['docs']}/{meta['targets']}文書・薬効薬理の記載あり{meta['with_ep']}・中身{meta['bodies']}種類")
    return meta


# ---------------------------------------------------------------- サイト生成時
BUCKETS = 64   # サイトの中身ファイルの数(docs/yakuri/b/00.json〜63.json)。中身の番号の頭2桁で振り分ける(ページのJSにも build_site が同じ数を渡す)
_IF_KEY = re.compile(r"(\d{6}_[0-9A-Z]{12})")   # 企業コード_YJコード。添付文書とIFのPDFコードに共通で入っている


def bucket(h: str) -> str:
    return f"{int(h[:2], 16) % BUCKETS:02d}"


def if_map(if_idx: dict | None) -> dict[str, str]:
    """{企業コード_YJコード: IFのPDF(if_index の "u"。IF_BASE からの続き or 丸ごとのURL)}"""
    out: dict[str, str] = {}
    for it in (if_idx or {}).get("items") or []:
        for fx in it.get("f") or []:
            m = _IF_KEY.search(fx.get("u") or "")
            if m:
                out.setdefault(m.group(1), fx["u"])
    return out


def site_data(tenpu: dict | None, store: dict | None, if_idx: dict | None) -> tuple[list[dict], dict[str, dict]]:
    """サイト用: (製品の一覧, {ファイル番号: {中身の番号: {"b": ブロック}}}) を返す。
    製品の一覧は添付文書一覧の行ごと {"n": 販売名, "g": 一般名, "c": 会社, "u": PDFコード, "h": 中身の番号("" = 章なし), "i": IFのPDF(あれば)}。
    写しがまだ無い文書(記録なし)の製品は載せない"""
    docs = (store or {}).get("docs") or {}
    bodies = (store or {}).get("bodies") or {}
    ifs = if_map(if_idx)
    items: list[dict] = []
    used: set[str] = set()
    for it in (tenpu or {}).get("items") or []:
        u = next((fx.get("u") for fx in it.get("f") or [] if fx.get("u") in docs), "")
        if not u:
            continue
        h = docs[u][1]
        if h and h not in bodies:
            continue
        rec = {"n": it.get("n") or "", "g": it.get("g") or "", "c": it.get("c") or "", "u": u, "h": h}
        m = _IF_KEY.search(u)
        if m and m.group(1) in ifs:
            rec["i"] = ifs[m.group(1)]
        items.append(rec)
        if h:
            used.add(h)
    files: dict[str, dict] = {f"{i:02d}": {} for i in range(BUCKETS)}
    for h in sorted(used):
        files[bucket(h)][h] = bodies[h]
    return items, files


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    refresh(Path(__file__).resolve().parent.parent)
