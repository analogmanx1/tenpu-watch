# -*- coding: utf-8 -*-
"""
副作用検索の元データを作る。添付文書XMLの「11. 副作用」の章から、副作用を「頻度の帯 × 区分(肝臓・消化器など)」ごとに抜き出して
data/fukusayo_index.json に溜める。

- 読むのは **PC内のXMLの写し(xml_cache.py)だけ**。PMDAへはアクセスしない(写しは識別コード・注意チェックの取得で毎日そろう)
  → 毎日の自動更新(run.py --shikibetsu)のついでに、新しい文書・改版された文書だけ読み直す。写しがまだ無い文書は次回拾う
- 書き手は自宅PCだけ(衝突時は ours。scripts/local_update.ps1 の Resolve-StuckMerge に登録済み)
- 中身がまったく同じ添付文書(後発品どうしなど)は1つにまとめて持つ("docs" は文書→中身の番号、"bodies" が中身)
  → サイトでは「同じ記載の製品: ◯件」としてまとめて見せる

添付文書の書き方(新記載要領のXML):
  重大な副作用 … <SeriousAdverseEvents> の各見出し「劇症肝炎（頻度不明）、肝機能障害、黄疸（0.1%未満）」→ カッコの頻度を前の名前に付ける
                見出しに頻度が無いときは、説明文 → 章の前置き の順に「（頻度不明）」などを探す。無ければ「頻度の記載なし」
  その他の副作用 … <OtherAdverse> の表(区分の行 × 頻度の列)。効能ごとに表が分かれるとき(〈パーキンソン病〉など)は表の見出しを添える
  ※ 頻度はほとんどが「5%以上」「1～5%未満」のような帯。項目ごとのピッタリの%は一部の添付文書にしか書かれていない

使い方: python src/fukusayo_index.py          (写しから差分更新。初回・DATA_VERSION を上げた直後は全文書で数分)
        python src/run.py --shikibetsu        (毎日の自動実行と同じ: 識別コード・注意チェックのついでに更新)
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import unicodedata
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pmda_watch  # noqa: E402  (XMLの名前空間)
import shikibetsu_index  # noqa: E402  (対象文書の洗い出しを流用)
import xml_cache  # noqa: E402  (XMLのPC内の写し)

JST = timezone(timedelta(hours=9))
NS = pmda_watch.NS
OUT_NAME = "fukusayo_index.json"
DATA_VERSION = 1            # 抜き出し方・記録の形式。変えたら上げる(全文書を写しから読み直す=数分)
NO_FREQ = "頻度の記載なし"
SERIOUS = "重大な副作用"
_FREQ = re.compile(r"[0-9０-９.．]+\s*[%％]|頻度不明")
# 頻度の欄・見出しに付く注の印(注1）・a）・※など)。<Sup> の中身がこれなら落とす
_NOTE = re.compile(r"^\s*(?:注\s*[0-9０-９a-zA-Z]*\s*[）)]?|[a-zA-Zａ-ｚ*＊※†‡§]+\s*[）)]?|[0-9０-９]+\s*[）)]|[,、\s]+)+\s*$")


def log(msg: str) -> None:
    print(f"[{datetime.now(JST).strftime('%H:%M:%S')}] {msg}", flush=True)


def _local(tag) -> str:
    return tag.replace(NS, "") if isinstance(tag, str) else ""


def _text(el) -> str:
    """要素内の文字。注の印だけの <Sup> は落とす(「0.1%未満注2）」→「0.1%未満」)"""
    out: list[str] = []

    def rec(e, top=False):
        if isinstance(e.tag, str):
            if _local(e.tag) == "Sup" and _NOTE.match("".join(e.itertext())):
                pass
            else:
                if e.text:
                    out.append(e.text)
                for c in e:
                    rec(c)
        if e.tail and not top:
            out.append(e.tail)

    rec(el, top=True)
    return re.sub(r"\s+", " ", "".join(out)).strip()


def norm_band(s: str) -> str:
    """頻度の帯の表記をそろえる: 「0.1%～5%未満」「0.1〜5％未満」「1%以上5%未満」→「0.1～5%未満」「1～5%未満」"""
    s = unicodedata.normalize("NFKC", s or "").strip()
    s = re.sub(r"[〜~∼]", "～", s)
    s = re.sub(r"\s+", "", s)
    s = re.sub(r"(注\d*\)?|[a-z]\))+$", "", s)          # 頻度の欄の末尾に残った注の印
    s = re.sub(r"^(?:いずれも|すべて|全て)", "", s)
    s = re.sub(r"(\d)%～", r"\1～", s)
    s = re.sub(r"(\d)%以上(\d)", r"\1～\2", s)
    s = re.sub(r"(\d)%以上～", r"\1～", s)
    return s or NO_FREQ


def bounds(s: str) -> tuple[float, float]:
    """帯の (下限, 上限)。「5%以上」=(5, 1000)・「1～5%未満」=(1, 5)・「1%未満」=(0, 1)・「72.6%」=(72.6, 72.6)。頻度不明=(-1,-1)・記載なし=(-2,-2)"""
    s = norm_band(s)
    num = r"(\d+(?:\.\d+)?)"
    m = re.search(num + r"%?[～-]" + num + "%", s)
    if m:
        return (float(m.group(1)), float(m.group(2)))
    m = re.search(num + r"%(以上|超|未満|以下)", s)
    if m:
        v = float(m.group(1))
        return (v, 1000.0) if m.group(2) in ("以上", "超") else (0.0, v)
    nums = [float(x) for x in re.findall(num + "%", s)]
    if nums:
        # ピッタリの%(「72.6%」)。「3.9%、49.7%」のように複数並ぶときは大きいほう
        return (max(nums), max(nums))
    return (-2.0, -2.0) if s == NO_FREQ else (-1.0, -1.0)


def band_key(s: str) -> tuple[float, float]:
    """並べ順(大きいほど上)。「◯%以上」は下限、幅・「◯%未満」は上限、ピッタリの%はその値で比べ、同じなら下限が高いほうが上。頻度不明・記載なしは最後
    (例: 72.6% → 20%以上 → 5～20%未満 → 5%以上 → 0.1～5%未満 → 3.2% → 1%未満 → 0.5% → 0.1%未満 → 頻度不明)"""
    lo, hi = bounds(s)
    return (lo if hi >= 1000 else hi, lo)


def _freq_in(text: str) -> str:
    """文中の「（頻度不明）」「（0.1%未満）」などカッコ書きの頻度を1つ拾う"""
    m = re.search(r"[（(]([^（()）]*(?:[0-9０-９]\s*[%％]|頻度不明)[^（()）]*)[）)]", text or "")
    return m.group(1) if m else ""


def split_serious(header: str, fallback: str) -> list[tuple[str, str]]:
    """「劇症肝炎（頻度不明）、肝機能障害、黄疸（0.1%未満）」→ [(頻度不明, 劇症肝炎), (0.1%未満, 肝機能障害、黄疸)]
    頻度のカッコが無い名前は fallback(説明文などから拾った頻度)の帯にする"""
    out: list[tuple[str, str]] = []
    pos = 0
    for m in re.finditer(r"[（(]([^（()）]*(?:[0-9０-９]\s*[%％]|頻度不明)[^（()）]*)[）)]", header):
        name = header[pos:m.start()].strip("、,， ")
        if name:
            out.append((norm_band(m.group(1)), name))
        pos = m.end()
    rest = header[pos:].strip("、,， ")
    if rest:
        out.append((norm_band(fallback) if fallback else NO_FREQ, rest))
    return out


def extract(xml_text: str) -> dict | None:
    """1文書の副作用 → {"r": [[帯, 区分, 副作用の文言, 表の見出し], …], "x": 表になっていない本文(あれば)}。
    重大な副作用は 区分="重大な副作用"。副作用の章が無い文書は None"""
    root = ET.fromstring(xml_text)
    ae = next((e for e in root.iter(NS + "AdverseEvents")), None)
    if ae is None:
        return None
    rows: list[list[str]] = []
    free: list[str] = []
    for sec in ae.iter(NS + "SeriousAdverseEvents"):
        # 章の前置き・注に「いずれも頻度不明」などがあれば、頻度の無い見出しの帯に使う
        intro = " ".join(_text(x) for x in sec.iter() if _local(x.tag) in ("Instructions", "ExplanatoryNotes"))
        sec_default = _freq_in(intro) or ("頻度不明" if "頻度不明" in intro else "")
        for item in sec.iter(NS + "Item"):
            hd = item.find(NS + "Header")
            if hd is None:
                continue
            header = _text(hd)
            if not header:
                continue
            detail = " ".join(_text(d) for d in item.findall(NS + "Detail"))
            fb = "" if _FREQ.search(header) else (_freq_in(detail) or sec_default)
            for band, name in split_serious(header, fb):
                rows.append([band, SERIOUS, name, ""])
    for ev in ae.iter(NS + "OtherAdverseEvent"):
        label = " ".join(_text(x) for x in ev.findall(NS + "Instructions"))
        label = label if re.match(r"^[〈<＜]", label) else ""   # 〈効能〉の見出しだけ添える(前置きの文は添えない)
        tables = list(ev.iter(NS + "OtherAdverse"))
        got = False
        for tb in tables:
            cats = {c.get("id"): _text(c) for c in tb.iter(NS + "Category")}
            frqs = {c.get("id"): _text(c) for c in tb.iter(NS + "Frequency")}
            for d in tb.iter(NS + "AdverseReactionDescription"):
                t = _text(d)
                if not t:
                    continue
                rows.append([norm_band(frqs.get(d.get("frequencyRef"), "")), cats.get(d.get("categoryRef"), ""), t, label])
                got = True
        if not got:
            t = _text(ev)
            if t:
                free.append(t[:3000])
    if not rows and not free:
        return {"r": []}
    out: dict = {"r": rows}
    if free:
        out["x"] = "\n".join(free)
    return out


def _hash(body: dict) -> str:
    return hashlib.sha1(json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:12]


def load(root: Path) -> dict | None:
    f = Path(root) / "data" / OUT_NAME
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        log(f"!! data/{OUT_NAME} を読めませんでした({e})")
        return None


def _write(f: Path, obj: dict) -> None:
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp.json")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8", newline="\n")
    tmp.replace(f)


def refresh(root: Path) -> dict:
    """data/fukusayo_index.json を差分更新する(新しい文書・改版された文書だけ)。PC内のXMLの写しから読むだけ。中身が変わったときだけ書く
    docs = {PDFコード: [更新日, 中身の番号("" = 副作用の章なし), 形式の版]} / bodies = {中身の番号: extract() の結果}"""
    root = Path(root)
    tf = root / "data" / "tenpu_index.json"
    if not tf.exists():
        raise RuntimeError("data/tenpu_index.json がありません")
    targets = shikibetsu_index.list_targets(json.loads(tf.read_text(encoding="utf-8")))
    data = load(root) or {}
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
        "with_ae": sum(1 for r in docs.values() if r[1]),
        "bodies": len(bodies),
    }
    om = {k: v for k, v in (data.get("meta") or {}).items() if k != "updated_at"}
    if changed or om != meta:
        meta = {"updated_at": datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S"), **meta}
        _write(root / "data" / OUT_NAME, {"meta": meta, "docs": dict(sorted(docs.items())), "bodies": dict(sorted(bodies.items()))})
    else:
        meta = data.get("meta") or meta
    log(f"副作用: 今回{done}文書を写しから読み込み(写し待ち{no_cache}件・エラー{errors}件) / "
        f"全体 {meta['docs']}/{meta['targets']}文書・副作用の記載あり{meta['with_ae']}・中身{meta['bodies']}種類")
    return meta


# ---------------------------------------------------------------- サイト生成時: 帯ごとにまとめる
def bands(body: dict) -> list[dict]:
    """extract() の結果 → 帯の高い順の [{"f": 帯, "l": 色分け(3=5%以上 2=1%以上 1=1%未満 0=頻度不明・記載なし), "r": [[区分, 文言, 表の見出し], …]}]。
    同じ帯の中は 重大な副作用 → 表の順(区分の並びは添付文書のまま)"""
    groups: dict[str, list] = {}
    for band, cat, text, label in body.get("r") or []:
        groups.setdefault(band, []).append([cat, text, label])
    out = []
    for b in sorted(groups, key=band_key, reverse=True):
        rs = sorted(groups[b], key=lambda r: 0 if r[0] == SERIOUS else 1)
        lo, hi = bounds(b)
        lv = 0 if lo < 0 else 3 if lo >= 5 else 2 if (lo >= 1 or hi >= 5) else 1
        out.append({"f": b, "l": lv, "r": rs})
    return out


BUCKETS = 64   # サイトの中身ファイルの数(docs/fukusayo/b/00.json〜63.json)。中身の番号の頭2桁で振り分ける(ページのJSにも build_site が同じ数を渡す)


def bucket(h: str) -> str:
    return f"{int(h[:2], 16) % BUCKETS:02d}"


def site_data(tenpu: dict | None, store: dict | None) -> tuple[list[dict], dict[str, dict]]:
    """サイト用: (製品の一覧, {ファイル番号: {中身の番号: {"b": 帯ごと, "x": 表になっていない本文}}}) を返す。
    製品の一覧は添付文書一覧の行ごと {"n": 販売名, "g": 一般名, "c": 会社, "u": PDFコード, "h": 中身の番号("" = 副作用の章なし)}。
    写しがまだ無い文書(記録なし)の製品は載せない"""
    docs = (store or {}).get("docs") or {}
    bodies = (store or {}).get("bodies") or {}
    items: list[dict] = []
    used: set[str] = set()
    for it in (tenpu or {}).get("items") or []:
        u = next((fx.get("u") for fx in it.get("f") or [] if fx.get("u") in docs), "")
        if not u:
            continue
        h = docs[u][1]
        if h and h not in bodies:
            continue
        items.append({"n": it.get("n") or "", "g": it.get("g") or "", "c": it.get("c") or "", "u": u, "h": h})
        if h:
            used.add(h)
    files: dict[str, dict] = {f"{i:02d}": {} for i in range(BUCKETS)}
    for h in sorted(used):
        b = bodies[h]
        rec: dict = {"b": bands(b)}
        if b.get("x"):
            rec["x"] = b["x"]
        files[bucket(h)][h] = rec
    return items, files


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    refresh(Path(__file__).resolve().parent.parent)
