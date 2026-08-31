# -*- coding: utf-8 -*-
"""
錠剤・カプセルなどの「識別コード」(本体・PTPの印字)一覧を作る。
data/tenpu_index.json(添付文書一覧)の全文書について、
添付文書XMLを1件ずつ取得して「識別コード」欄を抜き出し、data/shikibetsu_index.json に保存する。
「識別コード検索」ページ(docs/shikibetsu/)の元データ。
※当初は剤形コードで錠(F)・カプセル(M)だけに絞っていたが、腸溶錠(H)・口腔用錠(E)・
  細粒と同じ文書の錠(リボトリール等)・英数字混じりの新形式コードが漏れたため全文書方式に変更(2026-08-30)

- PMDAは添付文書の個別ページを海外・クラウドIPからブロックしているため、自宅PC(日本)で実行する
- 20件ごとに途中保存。途中で止めても次回は続きから
- 2回目以降は「新しく増えた文書」と「添付文書の更新日が変わった文書」だけ取得(通常は数件〜数十件)
- 二重実行防止: data/.shikibetsu.lock がある間は他の実行がスキップする(タスクスケジューラとの同時実行対策)

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
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pmda_watch  # noqa: E402  (XML取得・本文抽出を流用)

JST = timezone(timedelta(hours=9))
NS = pmda_watch.NS
XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"
OUT_NAME = "shikibetsu_index.json"
LOCK_NAME = ".shikibetsu.lock"
SAVE_EVERY = 20             # この件数ごとに途中保存
SLEEP = 0.7                 # 1文書ごとの待ち(秒)。fetch_xml内の待ちと合わせて1文書2秒強


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


def extract_codes(xml_text: str) -> list[dict]:
    """添付文書XMLから識別コードを抜く。
    [{"c": コード, "b": 製品名(1文書に複数製品がある場合のみ), "l": 欄の名前(「識別コード」以外のみ)}]
    書き方は2パターンある(両対応):
      A) <PropertyTable> の中の専用タグ <IdCode>
      B) <OtherProperty> の <CategoryName>識別コード</CategoryName> + <Content>…</Content>(自由記述)
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

    def add(text: str, brand: str | None, label: str | None) -> None:
        c = _clean(text)
        if not c:
            return
        rec: dict = {"c": c}
        if multi and brand:
            rec["b"] = brand
        if label and label != "識別コード":
            rec["l"] = label
        if rec not in codes:
            codes.append(rec)

    def collect(scope, brand: str | None) -> None:
        for el in scope.iter():
            if not isinstance(el.tag, str):
                continue
            if el.tag == NS + "IdCode":                       # パターンA
                for t in _ja_texts(el):
                    add(t, brand, None)
                continue
            cat = None                                        # パターンB
            for ch in el:
                if ch.tag == NS + "CategoryName":
                    ts = _ja_texts(ch)
                    cat = _clean(ts[0]) if ts else None
            if not cat or not _is_code_category(cat):
                continue
            for ch in el:
                if ch.tag != NS + "Content":
                    continue
                for t in _ja_texts(ch):
                    add(t, brand, cat)

    pfbs = list(root.iter(NS + "PropertyForBrand"))
    for p in pfbs:
        collect(p, brands.get(p.get("ref") or ""))
    if not codes:   # 性状の表が PropertyForBrand の外にある(または無い)文書のための保険
        collect(root, None)
    return codes


# ---------------------------------------------------------------- XML取得
def fetch_xml_direct(u: str) -> str | None:
    """XMLを直接取得する。URLは /go/xml/{企業コード_packins番号} で組み立てられる(=PDFコードそのまま)。
    404のとき(掲載差し替えで版がずれた等)だけ、旧方式(添付文書ページ経由・+5版まで探索)に切り替える。
    1文書1リクエストで済むぶん速く、PMDAへの負荷も半分"""
    try:
        data = pmda_watch.http_get(f"{pmda_watch.BASE}/go/xml/{u}")
    except pmda_watch.NotFound:
        return pmda_watch.fetch_xml(u.split("_", 1)[1])[0]
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
        names = [n for n in zf.namelist() if n.lower().endswith(".xml")]
        return zf.read(names[0]).decode("utf-8", "replace") if names else None
    except zipfile.BadZipFile:
        return data.decode("utf-8", "replace")   # 生XMLで返ってきた場合


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
                if u not in docs or (docs[u].get("t") or "") != t
                or (retry_errors and docs[u].get("err"))]
        capped = bool(max_docs) and len(todo) > max_docs
        log(f"対象 {len(targets)}件(全添付文書) / 今回取得 {len(todo)}件"
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
                "pending": len([u for u, t in targets.items()
                                if u not in docs or (docs[u].get("t") or "") != t]),
                "errors": sum(1 for r in docs.values() if r.get("err")),
            }
            # 書き込み途中を他プロセス(gitの自動コミット等)に読まれないよう、一時ファイル→置き換えで保存
            tmp = out.with_suffix(".tmp.json")
            tmp.write_text(json.dumps(store, ensure_ascii=False, separators=(",", ":")),
                           encoding="utf-8", newline="\n")
            tmp.replace(out)

        done = fails = net_fails = 0
        for i, u in enumerate(todo, 1):
            rec: dict = {"t": targets[u], "at": datetime.now(JST).strftime("%Y-%m-%d")}
            try:
                xml_text = fetch_xml_direct(u)
                if not xml_text:
                    rec["err"] = "XMLが見つからない"
                else:
                    rec["codes"] = extract_codes(xml_text)
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
            docs[u] = rec
            done += 1
            n = len(rec.get("codes") or [])
            log(f"  ({i}/{len(todo)}) {u} → コード{n}件" + (f" ! {rec['err']}" if rec.get("err") else ""))
            if done % SAVE_EVERY == 0:
                save()
            time.sleep(SLEEP)
        save()
        m = store["meta"]
        log(f"保存: {out} (今回{done}件・エラー{fails}件 / 全体: コードあり{m['with_codes']}文書・{m['codes']}件、未取得{m['pending']}件)")
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
