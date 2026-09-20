# -*- coding: utf-8 -*-
"""
「術前休薬・禁忌チェック」の元データを作る。
添付文書XMLの決まった章(禁忌・重要な基本的注意など)から、手術に触れている文を集めて data/shujutsu_index.json に溜める。

2段構えにしてある(あとから条件を直しても、PMDAから取り直さなくて済むように):
  1) 取得時 … extract(): 見る章の中で「手術/術前/術中/術後/周術期」を含む文を **広めに** 拾って保存(候補)
  2) サイト生成時 … classify() / build_items(): 候補を 🚫禁忌 / ⏸手術時の対応あり / 📎その他 に仕分けて一般名でまとめる
→ 仕分けの言葉(NOT_SURG・ACT)、区分ごとの章(STOP_CH・OTHER_CH)、外す薬効分類(USED_IN_SURGERY)を直したら
  `python src/run.py --build-only` だけで反映される。広めの網(NET)や見る章(CHAPTERS)を変えたときだけ DATA_VERSION を上げる
  (=全文書が取り直し対象。約1.1万件・3〜5時間)

- XMLの取得は識別コード一覧(shikibetsu_index.py)と同じ(PMDAの個別ページは海外IPブロックのため自宅PC専用)
- 毎日の自動実行では、識別コードの取得で落としてきたXMLをそのまま受け取る(feed)ので、PMDAへのアクセスは増えない
- 20件ごとに途中保存。途中で止めても次回は続きから。二重実行防止は data/.shujutsu.lock

使い方: python src/shujutsu_index.py            (まだ取れていない文書を全部取得。初回は約1.1万件・3〜5時間)
        python src/shujutsu_index.py --max 300  (最大300件で切り上げ)
        python src/shujutsu_index.py --retry-errors
        python src/run.py --shikibetsu          (毎日の自動実行と同じ: 識別コードのついでに差分更新)
"""
from __future__ import annotations

import difflib
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pmda_watch  # noqa: E402
import shikibetsu_index  # noqa: E402  (XMLの取得・対象文書の洗い出しを流用)

JST = timezone(timedelta(hours=9))
NS = pmda_watch.NS
OUT_NAME = "shujutsu_index.json"
LOCK_NAME = ".shujutsu.lock"
DATA_VERSION = 1            # 保存する候補の形式。NET・CHAPTERS を変えたら上げる(全文書が取り直し対象になる)
SAVE_EVERY = 20
SLEEP = 0.7
TEXT_MAX = 1000             # これより長い文(表など)は、手術に触れている文だけ保存する
BODY_MAX = 300              # 見出しがヒットしたときに添える説明文の長さ

# ---- 取得時: 見る章(XMLのタグ → 章名)と広めの網。効能・用法の本文、副作用、薬物動態、臨床成績は見ない(「手術不能乳癌」等のノイズ源)
CHAPTERS = {
    "Warnings": "1. 警告",
    "ContraIndications": "2. 禁忌",
    "EfficacyRelatedPrecautions": "5. 効能又は効果に関連する注意",
    "InfoPrecautionsDosage": "7. 用法及び用量に関連する注意",
    "ImportantPrecautions": "8. 重要な基本的注意",
    "UseInSpecificPopulations": "9. 特定の背景を有する患者に関する注意",
    "Interactions": "10. 相互作用",
    "PrecautionsForApplication": "14. 適用上の注意",
    "OtherPrecautions": "15. その他の注意",
}
NET = re.compile(r"手術|術[前中後]|周術期")
# 見ない章(ここに無いタグは、知らない章でも念のため候補として保存する。仕分けで使うのは CHAPTERS にある章だけ)
SKIP_TAGS = {
    "PackageInsertNo", "CompanyIdentifier", "DateOfPreparationOrRevision", "Sccj", "TherapeuticClassification",
    "ApprovalEtc", "GenericName", "CompositionAndProperty", "IndicationsOrEfficacy", "InfoDoseAdmin", "AdverseEvents",
    "InfluenceOnLaboratoryValues", "OverDosage", "Pharmacokinetics", "ResultsOfClinicalTrials", "EfficacyPharmacology",
    "PhyschemOfActIngredients", "PrecautionsForHandling", "ConditionsOfApproval", "Package", "MainLiterature",
    "AddresseeOfLiteratureRequest", "AttentionOfInsurance", "NameAddressManufact", "ReferenceInformation",
    "SpeciallyDescribedItems",
}

# ---- サイト生成時: 仕分けの条件(ここを直したら --build-only だけで反映)
SURG = re.compile(r"手術|(?<![一-龥])術[前中後]|周術期")   # 「弁置換術後」のような術式名の一部は拾わない
# 手術の「時期」の話ではない言い回し(適応症の名前・既往歴など)は、消してから判定する
NOT_SURG = re.compile(r"〈[^〉]*〉|[A-Za-zＡ-Ｚａ-ｚ一-龥]*手術施行(?:後|患者)|手術不能|手術不可能|術[前後](?:・術後)?(?:補助|薬物療法)|手術の補助療法"
                      r"|術後(?:疼痛|鎮痛|感染|悪心|嘔吐|回復液|放射線|イレウス)|手術等による|緊急手術が必要"
                      r"|手術(?:の)?既往|手術歴|手術療法|手術適応|手術野|手術部位|手術創")
# 「手術のときどうするか」が書いてある文の目印(中止・休薬・◯日前・増量など)
ACT = re.compile(r"中止|休薬|中断|延期|投与しない|投与を避け|投与は避け|投与を控え|使用しない|使用を避け|投与を行わない|切り替え|切り換え"
                 r"|(?:時間|日|週間?|ヵ月|か月)(?:以上)?(?:経過|前|以内)|増量"
                 r"|手術を予定|手術が予定|手術予定|術前又は")   # 9章の「手術を予定している患者」「術前又は長期臥床状態の患者」も手術前の話なので⏸へ
TIER_KINKI, TIER_STOP, TIER_OTHER = 0, 1, 2   # 🚫禁忌 / ⏸手術時の対応あり / 📎その他
STOP_CH = ("Warnings", "InfoPrecautionsDosage", "ImportantPrecautions", "UseInSpecificPopulations", "Interactions")
OTHER_CH = ("Warnings", "ImportantPrecautions", "UseInSpecificPopulations", "Interactions", "OtherPrecautions")
# 手術で「使う側」の薬(薬効分類の頭3桁)。📎その他 からは外す(麻酔の深度・術中の観察などの説明ばかりになるため。禁忌・⏸は残す)
USED_IN_SURGERY = ("111", "112", "121", "122")   # 全身麻酔剤 / 催眠鎮静剤 / 局所麻酔剤 / 骨格筋弛緩剤


def log(msg: str) -> None:
    print(f"[{datetime.now(JST).strftime('%H:%M:%S')}] {msg}", flush=True)


def sentences(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"(?<=。)|\n", text or "") if p.strip()]


def _surg(s: str) -> bool:
    return bool(SURG.search(NOT_SURG.sub("", s)))


# ---------------------------------------------------------------- 取得時: XMLから候補を抜く
def extract(xml_text: str) -> dict:
    """{"cls": 薬効分類名, "c": [{"ch": 章タグ, "p": 見出しパス, "t": 本文, "b": 見出しに続く説明文}]} を返す"""
    parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True, insert_pis=True))
    root = ET.fromstring(xml_text, parser=parser)
    cls = ""
    cands: list[dict] = []
    for ch in root:
        if not isinstance(ch.tag, str):
            continue
        tag = ch.tag.replace(NS, "")
        if tag == "TherapeuticClassification":
            cls = " / ".join(t for t in (pmda_watch.text_of(x) for x in ch.iter(NS + "Lang")) if t)[:80]
            continue
        if tag in SKIP_TAGS:
            continue
        lines, _rev = pmda_watch.walk_doc(ch)
        # 参照記号(HeaderRef)の名残で文末に付く「,」を落とす
        lines = [(p_, re.sub(r"(?<=。),+|[,\s]+$", "", t_)) for p_, t_ in lines]
        partner: dict[str, list[str]] = {}
        if tag == "Interactions":
            # 相互作用の表: どの併用薬の行かを添える(<Drug><DrugName>…</DrugName><ClinSymptomsAndMeasures>…)
            for drug in ch.iter(NS + "Drug"):
                names = [pmda_watch.text_of(x) for dn in drug.findall(NS + "DrugName") for x in dn.iter(NS + "Lang")]
                name = " ".join(n for n in names if n)[:80]
                for part in drug:
                    if part.tag == NS + "DrugName":
                        continue
                    for x in part.iter(NS + "Lang"):
                        t = re.sub(r"(?<=。),+|[,\s]+$", "", pmda_watch.text_of(x))
                        if t and name and name not in partner.setdefault(t, []):
                            partner[t].append(name)
        used: set[int] = set()   # 見出しの説明文として添えた行(単独の候補にはしない)
        for i, (path, text) in enumerate(lines):
            if i in used or not NET.search(text):
                continue
            c: dict = {"ch": tag, "p": path, "t": text}
            if text in partner:
                c["p"] = "併用薬: " + " / ".join(partner[text])
            if len(text) > TEXT_MAX:
                c["t"] = " … ".join(s for s in sentences(text) if NET.search(s))[:TEXT_MAX]
            if path and path.split(" > ")[-1] == text:
                # 見出しそのものがヒット(9章の「手術を予定している患者」等)→ 直後の説明文を添える
                body = []
                for j in range(i + 1, len(lines)):
                    if lines[j][0] != path or lines[j][1] == text:
                        break
                    body.append(lines[j][1])
                    used.add(j)
                if body:
                    c["b"] = " ".join(body)[:BODY_MAX]
                c["p"] = " > ".join(path.split(" > ")[:-1])
            cands.append(c)
    return {"cls": cls, "c": cands}


# ---------------------------------------------------------------- サイト生成時: 仕分け
def classify(rec: dict, u: str) -> list[dict]:
    """1文書ぶんの候補を仕分ける → [{"k": 区分, "ch": 章名, "p": パス, "t": 本文, "b": 説明文, "s": [手術に触れている文]}]"""
    yj3 = (u.split("_") + [""])[1][:3]
    hits = []
    for c in rec.get("c") or []:
        tag = c.get("ch") or ""
        sents = [s for s in sentences(c.get("t") or "") if _surg(s)]
        if not sents:
            continue
        if yj3 in USED_IN_SURGERY and tag == "InfoPrecautionsDosage":
            continue   # 麻酔薬などの「用法の注意」は手術での使い方の説明(休薬の話ではない)
        if tag == "ContraIndications":
            tier = TIER_KINKI
        elif tag in STOP_CH and any(ACT.search(s) for s in sents + sentences(c.get("b") or "")):
            tier = TIER_STOP
        elif tag in OTHER_CH and yj3 not in USED_IN_SURGERY:
            tier = TIER_OTHER
        else:
            continue
        hits.append({"k": tier, "ch": CHAPTERS.get(tag, tag), "p": c.get("p") or "", "t": c.get("t") or "",
                     "b": c.get("b") or "", "s": sents})
    return hits


def _norm_g(g: str) -> str:
    return re.sub(r"水和物$", "", (g or "").strip())


def _key(h: dict) -> str:
    return re.sub(r"[\s・\-－‐―,，、.。()()]", "", "".join(h["s"]))


def build_items(tenpu: dict | None, store: dict | None) -> list[dict]:
    """サイト用: 一般名でまとめる。メーカーごとの細かい言い回しの違いは、似た文を1つに束ねて一番多い表現を代表にする"""
    docs = (store or {}).get("docs") or {}
    groups: dict[str, dict] = {}
    for it in (tenpu or {}).get("items") or []:
        for fx in it.get("f") or []:
            u = fx.get("u") or ""
            rec = docs.get(u)
            if not rec or not rec.get("c"):
                continue
            hits = classify(rec, u)
            if not hits:
                continue
            g = groups.setdefault(_norm_g(it.get("g")) or it.get("n") or u,
                                  {"cls": Counter(), "y": "9999", "b": {}, "h": []})
            if rec.get("cls"):
                g["cls"][rec["cls"]] += 1
            g["y"] = min(g["y"], (u.split("_") + [""])[1][:4] or "9999")
            g["b"].setdefault(it.get("n") or u, u)
            for h in hits:
                g["h"].append(dict(h, u=u, n=it.get("n") or ""))
    items = []
    for name, g in groups.items():
        clusters: list[dict] = []   # {"k","ch","key","vars": Counter(key), "rep": {key: hit}}
        for h in g["h"]:
            k = _key(h)
            for cl in clusters:
                if cl["k"] == h["k"] and cl["ch"] == h["ch"] and (
                        k in cl["rep"] or difflib.SequenceMatcher(None, k, cl["key"]).ratio() >= 0.8):
                    cl["vars"][k] += 1
                    cl["rep"].setdefault(k, h)
                    break
            else:
                clusters.append({"k": h["k"], "ch": h["ch"], "key": k, "vars": Counter({k: 1}), "rep": {k: h}})
        hs = []
        for cl in clusters:
            h = cl["rep"][cl["vars"].most_common(1)[0][0]]
            hs.append({x: h[x] for x in ("k", "ch", "p", "t", "b", "s", "u", "n") if h.get(x) not in ("", None)})
        hs.sort(key=lambda h: (h["k"], h["ch"]))
        cls = (g["cls"].most_common(1) or [("", 0)])[0][0]
        cls = re.split(r"\n| / ", cls)[0].strip(" -－‐")   # 薬効分類名が複数行・複数あるときは先頭だけ
        items.append({"g": name, "c": cls, "y": g["y"],
                      "k": min(h["k"] for h in hs), "h": hs,
                      "b": [{"n": n, "u": u} for n, u in sorted(g["b"].items())]})
    items.sort(key=lambda x: (x["y"], x["g"]))
    return items


# ---------------------------------------------------------------- 保存データ(取得と差分更新)
class Store:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.out = self.root / "data" / OUT_NAME
        self.data: dict = {"meta": {}, "docs": {}}
        if self.out.exists():
            try:
                self.data = json.loads(self.out.read_text(encoding="utf-8"))
            except Exception as e:  # noqa: BLE001
                bad = self.out.with_suffix(".bad.json")
                self.out.replace(bad)
                log(f"!! {OUT_NAME} が読めないため作り直します({e})。壊れたファイルは {bad.name} に退避")
        self.docs: dict = self.data.setdefault("docs", {})
        self.fed = 0

    def feed(self, u: str, t: str, xml_text: str) -> None:
        """取得済みのXMLを受け取って記録する(識別コードの取得のついでに呼ばれる。保存は refresh() の最後)"""
        rec: dict = {"t": t, "at": datetime.now(JST).strftime("%Y-%m-%d"), "v": DATA_VERSION}
        try:
            ex = extract(xml_text)
            if ex["c"]:
                rec["cls"] = ex["cls"]
                rec["c"] = ex["c"]
        except Exception as e:  # noqa: BLE001
            rec["err"] = f"{type(e).__name__}: {e}"
        self.docs[u] = rec
        self.fed += 1

    def stale(self, u: str, t: str, retry_errors: bool = False) -> bool:
        r = self.docs.get(u)
        if r is None or (r.get("t") or "") != t or r.get("v") != DATA_VERSION:
            return True
        return bool(retry_errors and r.get("err"))

    def save(self, targets: dict[str, str]) -> dict:
        self.data["meta"] = {
            "updated_at": datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S"),
            "targets": len(targets),
            "fetched": sum(1 for u in targets if u in self.docs),
            "with_cands": sum(1 for r in self.docs.values() if r.get("c")),
            "pending": sum(1 for u, t in targets.items() if self.stale(u, t)),
            "errors": sum(1 for r in self.docs.values() if r.get("err")),
        }
        tmp = self.out.with_suffix(".tmp.json")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8", newline="\n")
        tmp.replace(self.out)
        return self.data["meta"]


def refresh(root: Path, max_docs: int = 0, retry_errors: bool = False, store: Store | None = None) -> dict:
    root = Path(root)
    tenpu_f = root / "data" / "tenpu_index.json"
    if not tenpu_f.exists():
        raise RuntimeError("data/tenpu_index.json がありません(先に python src/run.py --tenpu-index で添付文書一覧を作ってください)")
    tenpu = json.loads(tenpu_f.read_text(encoding="utf-8"))
    store = store or Store(root)

    lock = root / "data" / LOCK_NAME
    if lock.exists():
        try:
            age = time.time() - lock.stat().st_mtime
        except OSError:
            age = 0
        if age < 6 * 3600:
            # 保存もしない(実行中の別プロセスの進み具合を古い内容で上書きしないため。受け取ったぶんは次回取り直す)
            log("別の手術時チェックの取得が実行中のようなのでスキップします(data/.shujutsu.lock)")
            return store.data.get("meta") or {}
    lock.write_text(f"{os.getpid()} {datetime.now(JST)}", encoding="utf-8")

    def touch() -> None:
        try:
            lock.write_text(f"{os.getpid()} {datetime.now(JST)}", encoding="utf-8")
        except OSError:
            pass

    received = store.fed   # 識別コードの取得のついでに受け取った件数
    try:
        targets = shikibetsu_index.list_targets(tenpu)
        if len(targets) > 3000:
            for u in [u for u in store.docs if u not in targets]:
                del store.docs[u]
        todo = [u for u, t in targets.items() if store.stale(u, t, retry_errors)]
        # 新規・改版(中身が変わった)を先に、まだ一度も取っていない文書(初回の積み残し)を後に
        todo.sort(key=lambda u: 0 if u in store.docs else 1)
        capped = bool(max_docs) and len(todo) > max_docs
        log(f"手術時チェック: 対象 {len(targets)}件 / 受け取り済み {received}件 / 今回取得 {len(todo)}件"
            + (f" → 最大{max_docs}件で切り上げ(残りは次回)" if capped else ""))
        if max_docs:
            todo = todo[:max_docs]
        if todo:
            log(f"みこみ時間: 約{len(todo) * 1.4 / 60:.0f}分(PMDAに負荷をかけないよう1件ごとに待ち時間あり)")
        done = net_fails = 0
        for i, u in enumerate(todo, 1):
            try:
                xml_text, _zf = shikibetsu_index.fetch_doc(u)
                net_fails = 0
            except Exception as e:  # noqa: BLE001
                if "GET failed" in str(e):
                    net_fails += 1
                    log(f"  ! 通信エラー({net_fails}回目): {u}: {e}")
                    if net_fails >= 3:
                        log("!! 通信エラーが3回続いたため中断(取れた分は保存済み。次回続きから)")
                        break
                    time.sleep(5)
                    continue
                xml_text = None
                store.docs[u] = {"t": targets[u], "at": datetime.now(JST).strftime("%Y-%m-%d"), "v": DATA_VERSION,
                                 "err": f"{type(e).__name__}: {e}"}
            if xml_text:
                store.feed(u, targets[u], xml_text)
            elif u not in store.docs or not store.docs[u].get("err"):
                store.docs[u] = {"t": targets[u], "at": datetime.now(JST).strftime("%Y-%m-%d"), "v": DATA_VERSION,
                                 "err": "XMLが見つからない"}
            done += 1
            r = store.docs[u]
            if done % 50 == 0 or r.get("c") or r.get("err"):
                log(f"  ({i}/{len(todo)}) {u} → 候補{len(r.get('c') or [])}件" + (f" ! {r['err']}" if r.get("err") else ""))
            if done % SAVE_EVERY == 0:
                store.save(targets)
                touch()
            time.sleep(SLEEP)
        m = store.save(targets)
        log(f"保存: {store.out} (今回取得{done}件・受け取り{received}件 / "
            f"全体: 取得済み{m['fetched']}/{m['targets']}文書・手術の記載あり{m['with_cands']}文書・未取得{m['pending']}件・エラー{m['errors']}件)")
        return m
    finally:
        try:
            lock.unlink()
        except OSError:
            pass


if __name__ == "__main__":
    argv = sys.argv[1:]
    max_docs = int(argv[argv.index("--max") + 1]) if "--max" in argv else 0
    paths = [a for a in argv if not a.startswith("--") and not a.isdigit()]
    refresh(Path(paths[0]) if paths else Path(__file__).resolve().parent.parent,
            max_docs=max_docs, retry_errors="--retry-errors" in argv)
