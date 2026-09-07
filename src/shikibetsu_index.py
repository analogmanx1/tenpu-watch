# -*- coding: utf-8 -*-
"""
錠剤・カプセルなどの「識別コード」(本体・PTPの印字)一覧と外形画像を作る。
data/tenpu_index.json(添付文書一覧)の全文書について、添付文書XML(zip)を1件ずつ取得して
  - 「識別コード」欄の文字 → data/shikibetsu_index.json
  - 外形画像(表・裏)と、画像でしか載っていない識別コード → docs/shikibetsu/img/ に保存(zipに同梱されている)
「識別コード検索」ページ(docs/shikibetsu/)の元データ。
※当初は剤形コードで錠(F)・カプセル(M)だけに絞っていたが、腸溶錠(H)・口腔用錠(E)・
  細粒と同じ文書の錠(リボトリール等)・英数字混じりの新形式コードが漏れたため全文書方式に変更(2026-08-30)
※外形画像は2026-09-07に追加(DATA_VERSION=3。古い記録は自動で取り直し対象になる)

- PMDAは添付文書の個別ページを海外・クラウドIPからブロックしているため、自宅PC(日本)で実行する
- 20件ごとに途中保存。途中で止めても次回は続きから
- 2回目以降は「新しく増えた文書」と「添付文書の更新日が変わった文書」だけ取得(通常は数件〜数十件)
- 二重実行防止: data/.shikibetsu.lock がある間は他の実行がスキップする(タスクスケジューラとの同時実行対策)
- 画像は検索に載る文書(コードか画像コードがあるもの)だけ保存。参照されなくなった画像は実行の最後に掃除

使い方: python src/shikibetsu_index.py               (差分を全部取得。初回は4000件超・数時間)
        python src/shikibetsu_index.py --max 300     (最大300件で切り上げ)
        python src/shikibetsu_index.py --retry-errors (過去にエラーだった文書もやり直す)
        python src/run.py --shikibetsu               (毎日の自動実行と同じ: 最大300件。サイト生成つき)
"""
from __future__ import annotations

import io
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
import zipfile
import zlib
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pmda_watch  # noqa: E402  (XML取得・本文抽出を流用)

JST = timezone(timedelta(hours=9))
NS = pmda_watch.NS
XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"
OUT_NAME = "shikibetsu_index.json"
LOCK_NAME = ".shikibetsu.lock"
IMG_SUBDIR = ("docs", "shikibetsu", "img")   # 外形画像の置き場(サイトからは img/xxx.gif)
DATA_VERSION = 3            # 記録の形式。変えると全文書が取り直し対象になる(3=外形画像つき・文書ごとのファイル名)
SAVE_EVERY = 20             # この件数ごとに途中保存
SLEEP = 0.7                 # 1文書ごとの待ち(秒)。1文書1リクエストなので合わせて2秒弱
IMG_PER_UNIT = 2            # 1製品あたり保存する外形画像の枚数(表・裏)。側面は表裏が無いときだけ
IMG_MAX_BYTES = 150_000     # これより大きい画像(写真など)は保存しない(サイトの肥大化防止)
IMG_EXT = (".gif", ".png", ".jpg", ".jpeg")


def log(msg: str) -> None:
    print(f"[{datetime.now(JST).strftime('%H:%M:%S')}] {msg}", flush=True)


# ---------------------------------------------------------------- XMLから識別コードを抜く
def _ja_texts(el) -> list[str]:
    """要素の下の日本語 <Lang> テキストを全部集める"""
    out = []
    for l in el.iter(NS + "Lang"):
        if l.get(XML_LANG) in (None, "ja"):
            t = pmda_watch.text_of(l)
            if t:
                out.append(t)
    return out


def _clean(t: str) -> str:
    return re.sub(r"\s+", " ", t).strip()


def _is_code_category(cat: str) -> bool:
    """「識別コード」とみなすカテゴリ名か(自由記述パターン用)。
    実際の添付文書での書かれ方: 識別コード / 識別コード(PTP) / 本体表示 / 本体コード / 製剤表示 / 包装コード など"""
    key = re.sub(r"\s", "", cat)
    if "識別" in key and ("コード" in key or "記号" in key):
        return True
    return key in {"本体表示", "本体コード", "製剤表示", "包装コード",
                   "本体刻印", "本体印字", "PTP表示", "ＰＴＰ表示"}


def _shape_priority(cat: str) -> int:
    """自由記述パターンの外形画像の優先度。0=表・裏(平面/表面/裏面/外形 など呼び方はまちまち), 1=側面・断面。
    性状の表に画像が入る欄は外形の図がほぼ全てなので、欄名は列挙せず「側面っぽいものを後回し」だけにする"""
    key = re.sub(r"\s", "", cat)
    return 1 if any(w in key for w in ("側面", "側", "横", "断面")) else 0


def _graphics(el) -> list[str]:
    """要素の下の画像ファイル名(<InlineGraphic gfname=…>)を全部集める"""
    return [g.get("gfname") for g in el.iter(NS + "InlineGraphic") if g.get("gfname")]


def extract(xml_text: str) -> dict:
    """添付文書XMLから識別コードと外形画像の参照を抜く。戻り値:
      {"codes":  [{"c": コード, "b": 製品名(複数製品の文書のみ), "l": 欄の名前(「識別コード」以外のみ)}],
       "shapes": [{"g": [画像ファイル名(表, 裏)], "b": 製品名(同上)}],        … 外形画像
       "idimgs": [{"g": 画像ファイル名, "b": 製品名(同上)}]}                  … 画像でしか載っていない識別コード
    書き方は2パターンある(両対応):
      A) <PropertyTable> の中の専用タグ <IdCode> / <Shape><ShapeFront>…
      B) <OtherProperty> の <CategoryName>識別コード</CategoryName>(または 外形/表面/裏面) + <Content>…</Content>(自由記述)
    """
    # <?enter?>(改行)を落とさないよう、pmda_watch と同じPI保持パーサーで読む
    parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True, insert_pis=True))
    root = ET.fromstring(xml_text, parser=parser)
    brands: dict[str, str | None] = {}
    for b in root.iter(NS + "DetailBrandName"):
        if not b.get("id"):
            continue
        names: list[str] = []
        for a in b.iter(NS + "ApprovalBrandName"):
            names += _ja_texts(a)
        brands[b.get("id")] = names[0] if names else None
    multi = len(brands) > 1

    codes: list[dict] = []
    shapes: list[dict] = []
    idimgs: list[dict] = []
    seen_gf: set[str] = set()

    def with_brand(rec: dict, brand: str | None) -> dict:
        if multi and brand:
            rec["b"] = brand
        return rec

    def add_code(text: str, brand: str | None, label: str | None) -> None:
        c = _clean(text)
        if not c:
            return
        rec = with_brand({"c": c}, brand)
        if label and label != "識別コード":
            rec["l"] = label
        if rec not in codes:
            codes.append(rec)

    def add_shape(gfs: list[str], brand: str | None) -> None:
        # 先に枚数で切ってから既出を除く(同じ表を保険パスで再走査したとき、3枚目の側面が別エントリとして残らないように)
        gfs = [g for g in gfs[:IMG_PER_UNIT] if g not in seen_gf]
        if not gfs:
            return
        seen_gf.update(gfs)
        shapes.append(with_brand({"g": gfs}, brand))

    def add_idimg(gf: str, brand: str | None) -> None:
        if gf in seen_gf:
            return
        seen_gf.add(gf)
        idimgs.append(with_brand({"g": gf}, brand))

    def category_of(el) -> str | None:
        for ch in el:
            if ch.tag == NS + "CategoryName":
                ts = _ja_texts(ch)
                return _clean(ts[0]) if ts else None
        return None

    def scan_codes(scope, brand: str | None) -> None:
        """識別コード(文字・画像)を scope 全体から集める"""
        for el in scope.iter():
            if not isinstance(el.tag, str):
                continue
            if el.tag == NS + "IdCode":                       # パターンA
                for t in _ja_texts(el):
                    add_code(t, brand, None)
                for g in _graphics(el):
                    add_idimg(g, brand)
                continue
            cat = category_of(el)                             # パターンB
            if not cat or not _is_code_category(cat):
                continue
            for ch in el:
                if ch.tag != NS + "Content":
                    continue
                for t in _ja_texts(ch):
                    add_code(t, brand, cat)
                for g in _graphics(ch):
                    add_idimg(g, brand)

    def scan_shapes(table, brand: str | None) -> None:
        """性状の表1つぶん(1製品の1単位)の外形画像を集める。表ごとに表・裏の2枚"""
        loose: list[tuple[int, str]] = []   # 自由記述の外形画像(優先度, ファイル名)
        found_shape = False
        for el in table.iter():
            if not isinstance(el.tag, str):
                continue
            if el.tag == NS + "Shape":                        # パターンA: 外形(表・裏・側面)
                front_back: list[str] = []
                side: list[str] = []
                for ch in el:
                    if ch.tag in (NS + "ShapeFront", NS + "ShapeBack"):
                        front_back += _graphics(ch)
                    elif ch.tag == NS + "ShapeSide":
                        side += _graphics(ch)
                gfs = front_back + side
                if not gfs:                                   # 表裏側面の子が無い書き方なら直下の画像を使う
                    gfs = _graphics(el)
                if gfs:
                    add_shape(gfs, brand)
                    found_shape = True
                continue
            cat = category_of(el)                             # パターンB(コード欄以外で画像のある欄=外形)
            if not cat or _is_code_category(cat):
                continue
            pri = _shape_priority(cat)
            for ch in el:
                if ch.tag != NS + "Content":
                    continue
                # 「外形」欄の中で 表/裏/側面 が ContentTitle で分かれている書き方にも対応
                titles = [_clean(x) for sub in ch if sub.tag == NS + "ContentTitle" for x in _ja_texts(sub)]
                p = max([pri] + [_shape_priority(t) for t in titles])
                loose += [(p, g) for g in _graphics(ch)]
        if not found_shape and loose:
            # 表面・裏面・側面が別々の欄に書かれているタイプ。表裏を優先して2枚(sortは安定なので文書の順序は保たれる)
            loose.sort(key=lambda x: x[0])
            add_shape([g for _, g in loose], brand)

    def collect(scope, brand: str | None) -> None:
        scan_codes(scope, brand)
        # 1製品に性状の表が複数ある(規格違い・キット等)ときは表ごとに外形画像を持つ。表が無い書き方なら scope 全体を1つの表とみなす
        for tbl in list(scope.iter(NS + "PropertyTable")) or [scope]:
            scan_shapes(tbl, brand)

    pfbs = list(root.iter(NS + "PropertyForBrand"))
    for p in pfbs:
        collect(p, brands.get(p.get("ref") or ""))
    if not codes and not idimgs:
        # 性状の表が PropertyForBrand の外にある(または無い)文書のための保険。
        # 性状セクション(<Property>)の中だけを見る(文書全体を見ると構造式などの画像を外形と間違えるため)
        for p in root.iter(NS + "Property"):
            collect(p, None)
    return {"codes": codes, "shapes": shapes, "idimgs": idimgs}


def extract_codes(xml_text: str) -> list[dict]:
    """識別コードだけ欲しいとき用(テスト・互換)"""
    return extract(xml_text)["codes"]


# ---------------------------------------------------------------- XML取得
def fetch_doc(u: str) -> tuple[str | None, zipfile.ZipFile | None]:
    """添付文書のzip(XML+画像)を取得して (XML本文, zip) を返す。
    URLは /go/xml/{企業コード_packins番号} で組み立てられる(=PDFコードそのまま)。
    404のとき(掲載差し替えで版がずれた等)だけ、添付文書ページ経由で新しい版を探す(+5版まで)。
    1文書1リクエストで済むぶん速く、PMDAへの負荷も半分"""
    try:
        data = pmda_watch.http_get(f"{pmda_watch.BASE}/go/xml/{u}")
    except pmda_watch.NotFound:
        url, _used, _note = pmda_watch.find_xml_url(u.split("_", 1)[1])
        if not url:
            return None, None
        time.sleep(pmda_watch.SLEEP)
        data = pmda_watch.http_get(url)
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        return data.decode("utf-8", "replace"), None   # 生XMLで返ってきた場合(画像なし)
    names = [n for n in zf.namelist() if n.lower().endswith(".xml")]
    if not names:
        return None, zf
    return zf.read(names[0]).decode("utf-8", "replace"), zf


def fetch_xml_direct(u: str) -> str | None:
    """XML本文だけ欲しいとき用(テスト・互換)"""
    return fetch_doc(u)[0]


_MAGIC = (b"GIF8", b"\x89PNG", b"\xff\xd8\xff")   # 画像の先頭バイト(GIF/PNG/JPEG)。中身が画像でないものは置かない
_SAFE_NAME = re.compile(r"^[A-Za-z0-9_.\-]{1,150}$")


def save_images(zf: zipfile.ZipFile | None, gfnames: list[str], img_dir: Path, u: str) -> dict[str, str]:
    """zipの中の画像を img_dir に保存し、{XML上のファイル名: 保存したファイル名} を返す。
    保存名は「文書コード__元の名前」にして文書ごとに分ける(別文書で同名の画像が別の図を指すことがあるため)。
    大きすぎる画像・zipに無い画像・画像以外は保存しない(=参照からも外す)。1枚の失敗で他の画像や記録を巻き込まない"""
    saved: dict[str, str] = {}
    if zf is None or not gfnames:
        return saved
    members = {n.lower(): n for n in zf.namelist()}
    for g in gfnames:
        if not g.lower().endswith(IMG_EXT) or not _SAFE_NAME.match(g):
            continue
        name = members.get(g.lower())
        if not name:
            continue
        try:
            info = zf.getinfo(name)
            if info.file_size > IMG_MAX_BYTES or info.file_size == 0:
                continue
            data = zf.read(name)
            if not data.startswith(_MAGIC):
                continue
            stored = f"{u}__{g}"
            dest = img_dir / stored
            # 同名でも中身が変わっていれば書き直す(サイズとCRCで比較)
            if not dest.exists() or dest.stat().st_size != len(data) \
                    or (zlib.crc32(dest.read_bytes()) & 0xFFFFFFFF) != (info.CRC & 0xFFFFFFFF):
                dest.write_bytes(data)
            saved[g] = stored
        except (OSError, zipfile.BadZipFile, zlib.error) as e:
            log(f"    ! 画像を保存できず飛ばします: {g}: {type(e).__name__}: {e}")
    return saved


def build_record(t: str, xml_text: str, zf: zipfile.ZipFile | None, img_dir: Path, u: str) -> dict:
    """1文書ぶんの記録を作る。画像は「検索に載る文書」(コードか画像コードがある)だけ保存する。
    画像の保存に失敗しても識別コードの記録は守り、v を付けずに次回の取り直し対象に残す"""
    rec: dict = {"t": t, "at": datetime.now(JST).strftime("%Y-%m-%d"), "v": DATA_VERSION}
    ex = extract(xml_text)
    rec["codes"] = ex["codes"]
    if not ex["codes"] and not ex["idimgs"]:
        return rec
    wanted = [g for s in ex["shapes"] for g in s["g"]] + [d["g"] for d in ex["idimgs"]]
    try:
        ok = save_images(zf, wanted, img_dir, u)
    except Exception as e:  # noqa: BLE001
        log(f"    ! 画像の保存で問題(コードだけ記録して次回やり直し): {type(e).__name__}: {e}")
        del rec["v"]
        return rec
    shapes = [dict(s, g=[ok[g] for g in s["g"] if g in ok]) for s in ex["shapes"]]
    shapes = [s for s in shapes if s["g"]]
    idimgs = [dict(d, g=ok[d["g"]]) for d in ex["idimgs"] if d["g"] in ok]
    if shapes:
        rec["sh"] = shapes
    if idimgs:
        rec["ic"] = idimgs
    return rec


def prune_images(docs: dict, img_dir: Path) -> int:
    """どの文書からも参照されなくなった画像を消す(改版で画像ファイル名が変わったとき等)。消した数を返す。
    ※記録が空(作り直し直後など)のときは呼ばないこと(全部消えてしまう)"""
    used: set[str] = set()
    for r in docs.values():
        for s in r.get("sh") or []:
            used.update(s.get("g") or [])
        for d in r.get("ic") or []:
            if d.get("g"):
                used.add(d["g"])
    n = 0
    for f in img_dir.iterdir():
        if f.is_file() and f.suffix.lower() in IMG_EXT and f.name not in used:
            try:
                f.unlink()
                n += 1
            except OSError as e:
                log(f"    ! 画像を消せず飛ばします(次回また試す): {f.name}: {e}")
    return n


# ---------------------------------------------------------------- 対象の洗い出し
def list_targets(tenpu: dict) -> dict[str, str]:
    """添付文書一覧から {PDFコード u: 更新日 t} を返す。英語版PDF(末尾E)だけ除いて全文書が対象。
    剤形で絞らない: 識別コード欄が無い文書はコード0件になって検索に載らないだけなので、絞る必要がない"""
    targets: dict[str, str] = {}
    for it in tenpu.get("items") or []:
        for fx in it.get("f") or []:
            u = fx.get("u") or ""
            if not u or "_" not in u or u.endswith("E"):
                continue
            t = fx.get("t") or ""
            if u not in targets or t > targets[u]:
                targets[u] = t
    return targets


# ---------------------------------------------------------------- 更新本体
def refresh(root: Path, max_docs: int = 0, retry_errors: bool = False) -> dict:
    data_dir = root / "data"
    tenpu_f = data_dir / "tenpu_index.json"
    if not tenpu_f.exists():
        raise RuntimeError("data/tenpu_index.json がありません(先に python src/run.py --tenpu-index で添付文書一覧を作ってください)")
    tenpu = json.loads(tenpu_f.read_text(encoding="utf-8"))

    out = data_dir / OUT_NAME
    store: dict = {"meta": {}, "docs": {}}
    if out.exists():
        try:
            store = json.loads(out.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            # 数時間ぶんの取得結果を無言で捨てないよう、壊れたファイルは退避してから作り直す
            bad = out.with_suffix(".bad.json")
            out.replace(bad)
            log(f"!! {OUT_NAME} が読めないため作り直します({e})。壊れたファイルは {bad.name} に退避")
    docs: dict = store.setdefault("docs", {})
    img_dir = root.joinpath(*IMG_SUBDIR)
    img_dir.mkdir(parents=True, exist_ok=True)

    def failed_this_version(u: str, t: str) -> bool:
        """この版(t)の取得に失敗済みで、前回の記録を残している文書(同じ版を毎回やり直さない。--retry-errors で再試行)"""
        r = docs.get(u)
        return r is not None and r.get("err_t") == t

    def changed(u: str, t: str) -> bool:
        """新しく増えた・添付文書が改版された(=中身が変わった)文書"""
        r = docs.get(u)
        return r is None or ((r.get("t") or "") != t and not failed_this_version(u, t))

    def stale(u: str, t: str) -> bool:
        """取り直しが要る文書(中身が変わった or 記録の形式が古い)"""
        if failed_this_version(u, t):
            return False
        return changed(u, t) or docs[u].get("v") != DATA_VERSION

    def has_codes(u: str) -> bool:
        r = docs.get(u) or {}
        return bool(r.get("codes") or r.get("ic"))

    # 二重実行防止(6時間より古いロックは異常終了の残骸とみなして無視)
    lock = data_dir / LOCK_NAME
    if lock.exists():
        try:
            age = time.time() - lock.stat().st_mtime
        except OSError:
            age = 0
        if age < 6 * 3600:
            log("別の識別コード取得が実行中のようなのでスキップします(data/.shikibetsu.lock)")
            return store.get("meta") or {}
    lock.write_text(f"{os.getpid()} {datetime.now(JST)}", encoding="utf-8")

    try:
        targets = list_targets(tenpu)
        # 一覧から消えた文書(改版でPDFコードが変わった等)の古い記録は掃除する
        # ※一覧の取得が部分的に失敗している回で消しすぎないよう、対象が十分あるときだけ
        if len(targets) > 3000:
            for u in [u for u in docs if u not in targets]:
                del docs[u]
        todo = [u for u, t in targets.items()
                if stale(u, t) or (retry_errors and docs[u].get("err"))]
        # 新規・改版(中身が変わった)を先に、形式が古いだけの取り直しを後に(毎日の300件枠で鮮度を落とさないため。安定ソート)
        todo.sort(key=lambda u: 0 if changed(u, targets[u]) else 1)
        n_changed = sum(1 for u in todo if changed(u, targets[u]))
        capped = bool(max_docs) and len(todo) > max_docs
        log(f"対象 {len(targets)}件(全添付文書) / 今回取得 {len(todo)}件(新規・改版{n_changed}件、形式の取り直し{len(todo) - n_changed}件)"
            + (f" → 最大{max_docs}件で切り上げ(残りは次回)" if capped else ""))
        if max_docs:
            todo = todo[:max_docs]
        if todo:
            log(f"みこみ時間: 約{len(todo) * 1.4 / 60:.0f}分(PMDAに負荷をかけないよう1件ごとに待ち時間あり。混雑時は延びる)")

        def save() -> None:
            store["meta"] = {
                "updated_at": datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S"),
                "targets": len(targets),
                "fetched": len(docs),
                "with_codes": sum(1 for r in docs.values() if r.get("codes")),
                "codes": sum(len(r.get("codes") or []) for r in docs.values()),
                "with_images": sum(1 for r in docs.values() if r.get("sh") or r.get("ic")),
                "image_only": sum(1 for r in docs.values() if r.get("ic") and not r.get("codes")),
                "images": sum(len(s.get("g") or []) for r in docs.values() for s in r.get("sh") or [])
                          + sum(len(r.get("ic") or []) for r in docs.values()),
                "pending": len([u for u, t in targets.items() if changed(u, t)]),      # 新規・改版でまだ取れていない
                # 形式が古いだけの文書のうち、取り直せば外形図が付きうるもの(コードあり)。コード無し文書は取り直しても検索に載らないので数えない
                "migrating": len([u for u, t in targets.items() if not changed(u, t) and stale(u, t) and has_codes(u)]),
                "errors": sum(1 for r in docs.values() if r.get("err")),
            }
            # 書き込み途中を他プロセス(gitの自動コミット等)に読まれないよう、一時ファイル→置き換えで保存
            tmp = out.with_suffix(".tmp.json")
            tmp.write_text(json.dumps(store, ensure_ascii=False, separators=(",", ":")),
                           encoding="utf-8", newline="\n")
            tmp.replace(out)
            # ロックの時刻も更新(「6時間より古いロックは無視」が、長い実行でも「最後の保存から6時間」の意味になるように)
            try:
                lock.write_text(f"{os.getpid()} {datetime.now(JST)}", encoding="utf-8")
            except OSError:
                pass

        done = fails = net_fails = 0
        for i, u in enumerate(todo, 1):
            rec: dict = {"t": targets[u], "at": datetime.now(JST).strftime("%Y-%m-%d"), "v": DATA_VERSION}
            try:
                xml_text, zf = fetch_doc(u)
                if not xml_text:
                    rec["err"] = "XMLが見つからない"
                else:
                    rec = build_record(targets[u], xml_text, zf, img_dir, u)
                net_fails = 0
            except Exception as e:  # noqa: BLE001
                if "GET failed" in str(e):
                    # 通信自体の失敗は記録せず、次回やり直す
                    net_fails += 1
                    log(f"  ! 通信エラー({net_fails}回目): {u}: {e}")
                    if net_fails >= 3:
                        log("!! 通信エラーが3回続いたため中断(取れた分は保存済み。次回続きから)")
                        break
                    time.sleep(5)
                    continue
                rec["err"] = f"{type(e).__name__}: {e}"
                fails += 1
            prev = docs.get(u)
            if rec.get("err") and prev and (prev.get("codes") or prev.get("ic")):
                # 前回ちゃんと取れていた記録を、今回の失敗で消さない。失敗した版(err_t)を覚えて同じ版は再試行しない
                # (次に改版されるか --retry-errors のときに取り直す)
                prev["err"] = rec["err"]
                prev["err_t"] = targets[u]
                done += 1
                log(f"  ({i}/{len(todo)}) {u} ! {rec['err']} → 前回の記録を残します")
                if done % SAVE_EVERY == 0:
                    save()
                time.sleep(SLEEP)
                continue
            docs[u] = rec
            done += 1
            n = len(rec.get("codes") or [])
            n_img = sum(len(s["g"]) for s in rec.get("sh") or []) + len(rec.get("ic") or [])
            log(f"  ({i}/{len(todo)}) {u} → コード{n}件・画像{n_img}枚" + (f" ! {rec['err']}" if rec.get("err") else ""))
            if done % SAVE_EVERY == 0:
                save()
            time.sleep(SLEEP)
        save()
        # 記録が十分そろっているときだけ掃除する(作り直し直後などに全画像を消さないため)
        if len(targets) > 3000 and len(docs) > 3000:
            removed = prune_images(docs, img_dir)
            if removed:
                log(f"参照されなくなった画像を{removed}枚掃除")
        m = store["meta"]
        log(f"保存: {out} (今回{done}件・エラー{fails}件 / 全体: コードあり{m['with_codes']}文書・{m['codes']}件、"
            f"画像つき{m['with_images']}文書・{m['images']}枚(うち画像のみコード{m['image_only']}文書)、"
            f"未取得{m['pending']}件・形式の取り直し待ち{m['migrating']}件)")
        return m
    finally:
        try:
            lock.unlink()
        except OSError:
            pass


if __name__ == "__main__":
    argv = sys.argv[1:]
    max_docs = 0
    if "--max" in argv:
        max_docs = int(argv[argv.index("--max") + 1])
    retry = "--retry-errors" in argv
    paths = [a for a in argv if not a.startswith("--") and not a.isdigit()]
    root = Path(paths[0]) if paths else Path(__file__).resolve().parent.parent
    refresh(root, max_docs=max_docs, retry_errors=retry)
