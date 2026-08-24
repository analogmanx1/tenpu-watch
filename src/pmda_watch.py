# -*- coding: utf-8 -*-
"""
PMDA 添付文書 更新ウォッチ — 取得・解析モジュール

やること:
  1. 「過去1週間以内に更新された添付文書情報」ページ(1week.html)を取得して、
     日付ごとの「掲載分(更新/新規)」「削除分」を読み取る
  2. まだ記録していない行について、添付文書XMLを取得し、
     - 今回改訂箇所(modified="今回")を抜き出す
     - 前回保存した内容があれば、本文どうしの差分を取る
  3. 結果を data/days/YYYY-MM-DD.json に保存、最新本文を archive/docs/ に保存
標準ライブラリだけで動きます(requests不要)。
"""
from __future__ import annotations

import difflib
import html
import io
import json
import re
import sys
import time
import urllib.request
import urllib.error
import zipfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
import xml.etree.ElementTree as ET

BASE = "https://www.info.pmda.go.jp"
WEEK_URL = BASE + "/downfiles/ph/1week.html"
NS = "{http://info.pmda.go.jp/namespace/prescription_drugs/package_insert/1.0}"
UA = "pmda-tenpu-watch/1.0 (personal use; polite crawler)"
JST = timezone(timedelta(hours=9))
SLEEP = 0.7  # サーバーにやさしく(秒)


def now_jst() -> str:
    return datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")


def log(msg: str) -> None:
    print(f"[{datetime.now(JST).strftime('%H:%M:%S')}] {msg}", flush=True)


# ---------------------------------------------------------------- HTTP
class NotFound(Exception):
    pass


def http_get(url: str, retries: int = 3) -> bytes:
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise NotFound(url) from e
            last = e
            time.sleep(3 * (i + 1))
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = e
            time.sleep(3 * (i + 1))
    raise RuntimeError(f"GET failed: {url}: {last}")


# ---------------------------------------------------------------- 1week.html
def clean(s: str) -> str:
    s = re.sub(r"<BR\s*/?>", " / ", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


def parse_week_page(raw: bytes) -> list[dict]:
    """日付ごとのブロックに分けて返す。
    [{date:'2026-08-21', updates:[{brand,company,reason,packins_no,pack_url}], deletes:[{brand,company,reason,yj_hint}]}]
    """
    page = raw.decode("utf-8", errors="replace")
    # 日付見出しで分割
    parts = re.split(r'<td class="title">(\d{4})年(\d{2})月(\d{2})日</td>', page)
    # parts[0]=前置き, その後 (y,m,d,block) の繰り返し
    days = []
    for i in range(1, len(parts), 4):
        y, m, d, block = parts[i], parts[i + 1], parts[i + 2], parts[i + 3]
        date = f"{y}-{m}-{d}"
        updates, deletes = [], []
        # 掲載分 / 削除分 のテーブルを切り分け
        sec_ins = re.search(r"<h2>掲載分</h2>(.*?)</TABLE>", block, flags=re.S)
        sec_del = re.search(r"<h2>削除分</h2>(.*?)</TABLE>", block, flags=re.S)
        row_re = re.compile(r"<TR>\s*<TD>(.*?)</TD>\s*<TD>(.*?)</TD>\s*<TD>(.*?)</TD>\s*</TR>", re.S)
        if sec_ins:
            for c1, c2, c3 in row_re.findall(sec_ins.group(1)):
                m_href = re.search(r'HREF="/go/pack/([^/"]+)/?"', c1)
                packins = m_href.group(1) if m_href else None
                updates.append({
                    "brand": clean(c1),
                    "company": clean(c2),
                    "reason": clean(c3),
                    "packins_no": packins,
                    "pack_url": f"{BASE}/go/pack/{packins}/" if packins else None,
                })
        if sec_del:
            for c1, c2, c3 in row_re.findall(sec_del.group(1)):
                m_c = re.search(r"<!--\s*([0-9A-Z_]+)\s*-->", c1)
                deletes.append({
                    "brand": clean(c1),
                    "company": clean(c2),
                    "reason": clean(c3),
                    "yj_hint": m_c.group(1) if m_c else None,
                })
        days.append({"date": date, "updates": updates, "deletes": deletes})
    return days


# ---------------------------------------------------------------- XML 取得
def find_xml_url(packins_no: str) -> tuple[str | None, str, str | None]:
    """添付文書ページのフッター(foot)から XML の URL を拾う。
    リンク先が404(週内にさらに新しい版へ差し替わった等)なら版番号を +1〜+5 して探す。
    戻り値: (xml_url, 実際に使った packins_no, 注記)"""
    m = re.match(r"^(.+_)(\d+)$", packins_no)
    candidates = [packins_no]
    if m:
        base, ver = m.group(1), int(m.group(2))
        candidates += [f"{base}{ver + k:02d}" for k in range(1, 6)]
    last_err = None
    for i, cand in enumerate(candidates):
        try:
            foot = http_get(f"{BASE}/go/pack/{cand}?view=foot&lang=ja").decode("utf-8", "replace")
        except NotFound as e:
            last_err = e
            time.sleep(0.3)
            continue
        mm = re.search(r'href="(/go/xml/[^"]+)"', foot)
        note = None if i == 0 else f"掲載時のリンク先({packins_no})は既に無く、新しい版 {cand} を取得"
        return (BASE + mm.group(1) if mm else None), cand, note
    raise RuntimeError(f"添付文書ページが見つかりません(404): {packins_no}(+5版まで探索)")


def fetch_xml(packins_no: str) -> tuple[str | None, str | None, str, str | None]:
    """(xml_text, xml_url, 実際のpackins_no, 注記)。zip で返ってくるので展開する"""
    url, used, note = find_xml_url(packins_no)
    if not url:
        return None, None, used, note
    time.sleep(SLEEP)
    data = http_get(url)
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
        names = [n for n in zf.namelist() if n.lower().endswith(".xml")]
        if not names:
            return None, url, used, note
        return zf.read(names[0]).decode("utf-8", "replace"), url, used, note
    except zipfile.BadZipFile:
        # 生XMLで返ってきた場合
        return data.decode("utf-8", "replace"), url, used, note


# ---------------------------------------------------------------- XML 解析
def _tag(el) -> str:
    t = el.tag
    if not isinstance(t, str):
        return ""  # Comment / PI
    return t.replace(NS, "")


def text_of(el) -> str:
    """要素内の文字を、<?enter?> を改行にしながら集める"""
    out = []

    def rec(e):
        if e.tag is ET.ProcessingInstruction:
            if (e.text or "").strip().startswith("enter") or e.text == "enter":
                out.append("\n")
        elif e.tag is ET.Comment:
            pass
        else:
            if e.text:
                out.append(e.text)
            for c in e:
                rec(c)
        if e.tail:
            out.append(e.tail)

    if el.text:
        out.append(el.text)
    for c in el:
        rec(c)
    s = "".join(out)
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n\s*\n+", "\n", s)
    return s.strip()


def _header_text(el) -> str | None:
    for c in el:
        if _tag(c) == "Header":
            return text_of(c) or None
    return None


def _comment_title(txt: str) -> str:
    t = (txt or "").strip()
    return t


def walk_doc(root):
    """文書全体を歩いて、
       - lines: [(見出しパス, 本文)] … <Lang> 単位の平文(差分用)
       - revised: [{"path": 見出しパス, "text": 本文}] … modified="今回" の塊
    を返す
    """
    lines: list[tuple[str, str]] = []
    revised: list[dict] = []

    def walk(el, path: list[str], in_revised: bool):
        pending = None
        item_no = 0
        for child in el:
            if child.tag is ET.Comment:
                pending = _comment_title(child.text)
                continue
            if child.tag is ET.ProcessingInstruction:
                continue
            tag = _tag(child)
            own = pending
            pending = None
            if tag == "Item":
                item_no += 1
                if not own:
                    h = _header_text(child)
                    own = h if h else f"({item_no})"
            elif own is None and tag not in ("Lang", "Detail", "Header"):
                h = _header_text(child)
                if h:
                    own = h
            cpath = path + [own] if own else path
            is_rev = child.get("modified") == "今回"
            if tag == "Lang":
                t = text_of(child)
                if t:
                    lines.append((" > ".join(cpath), t))
                if is_rev and not in_revised:
                    revised.append({"path": cpath, "text": t})
                continue
            if is_rev and not in_revised:
                # この塊の中の文章をまとめて1件に
                langs = [text_of(x) for x in child.iter(NS + "Lang")]
                langs = [x for x in langs if x]
                revised.append({"path": cpath, "text": "\n".join(langs)})
                walk(child, cpath, True)
            else:
                walk(child, cpath, in_revised)

    walk(root, [], False)
    return lines, revised


def parse_packins_xml(xml_text: str) -> dict:
    parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True, insert_pis=True))
    root = ET.fromstring(xml_text, parser=parser)
    meta = {
        "packins_no": None, "company_id": None,
        "revision": {"current": None, "previous": None},
        "brands": [], "generic_name": None, "therapeutic_class": None,
    }
    for c in root:
        t = _tag(c)
        if t == "PackageInsertNo":
            meta["packins_no"] = (c.text or "").strip()
        elif t == "CompanyIdentifier":
            meta["company_id"] = (c.text or "").strip()
        elif t == "DateOfPreparationOrRevision":
            for p in c:
                if _tag(p) != "PreparationOrRevision":
                    continue
                ym = ver = None
                for q in p.iter():
                    if _tag(q) == "YearMonth":
                        ym = (q.text or "").strip()
                    if _tag(q) == "Lang" and ver is None:
                        ver = (q.text or "").strip()
                rec = {"ym": ym, "ver": ver}
                if p.get("id") == "今回":
                    meta["revision"]["current"] = rec
                elif p.get("id") == "前回":
                    meta["revision"]["previous"] = rec
        elif t == "GenericName":
            meta["generic_name"] = " / ".join(text_of(x) for x in c.iter(NS + "Lang") if text_of(x))
        elif t == "TherapeuticClassification":
            meta["therapeutic_class"] = " / ".join(text_of(x) for x in c.iter(NS + "Lang") if text_of(x))
        elif t == "ApprovalEtc":
            for b in c.iter(NS + "ApprovalBrandName"):
                for l in b.iter(NS + "Lang"):
                    tt = text_of(l)
                    if tt:
                        meta["brands"].append(tt)
    lines, revised = walk_doc(root)
    return {"meta": meta, "lines": lines, "revised": revised}


# ---------------------------------------------------------------- 差分
def diff_lines(old: list, new: list) -> list[dict]:
    """[(path,text)] どうしの差分。戻り値: [{"kind":"changed|added|removed","path":..,"before":..,"after":..}]"""
    okeys = [f"{p}\t{t}" for p, t in old]
    nkeys = [f"{p}\t{t}" for p, t in new]
    sm = difflib.SequenceMatcher(None, okeys, nkeys, autojunk=False)
    out = []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            continue
        olds = old[i1:i2]
        news = new[j1:j2]
        if op == "replace":
            # 同じ見出しどうしを対応付け、残りは追加/削除
            used = set()
            for p, t in olds:
                match = None
                for k, (p2, t2) in enumerate(news):
                    if k not in used and p2 == p:
                        match = k
                        break
                if match is None:
                    out.append({"kind": "removed", "path": p, "before": t, "after": None})
                else:
                    used.add(match)
                    out.append({"kind": "changed", "path": p, "before": t, "after": news[match][1]})
            for k, (p2, t2) in enumerate(news):
                if k not in used:
                    out.append({"kind": "added", "path": p2, "before": None, "after": t2})
        elif op == "delete":
            for p, t in olds:
                out.append({"kind": "removed", "path": p, "before": t, "after": None})
        elif op == "insert":
            for p, t in news:
                out.append({"kind": "added", "path": p, "before": None, "after": t})
    return out


def doc_key(packins_no: str) -> str:
    """'2399008F1020_7_04' → '2399008F1020_7'(版番号を除いた文書ID)"""
    m = re.match(r"^(.+)_\d+$", packins_no)
    return m.group(1) if m else packins_no


# ---------------------------------------------------------------- メイン処理
class Watcher:
    def __init__(self, root: Path):
        self.root = root
        self.days_dir = root / "data" / "days"
        self.arch_dir = root / "archive" / "docs"
        self.days_dir.mkdir(parents=True, exist_ok=True)
        self.arch_dir.mkdir(parents=True, exist_ok=True)

    def day_path(self, date: str) -> Path:
        return self.days_dir / f"{date}.json"

    def load_day(self, date: str) -> dict | None:
        p = self.day_path(date)
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
        return None

    def save_day(self, day: dict) -> None:
        self.day_path(day["date"]).write_text(
            json.dumps(day, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")

    def load_archive(self, key: str) -> dict | None:
        p = self.arch_dir / f"{key}.json"
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
        return None

    def save_archive(self, key: str, rec: dict) -> None:
        (self.arch_dir / f"{key}.json").write_text(
            json.dumps(rec, ensure_ascii=False), encoding="utf-8", newline="\n")

    def process_update(self, row: dict, date: str) -> dict:
        """1行(1添付文書)を処理して、サイト表示用の辞書にして返す"""
        entry = dict(row)
        entry.update({"fetched_at": now_jst(), "error": None, "xml_url": None, "note": None,
                      "pdf_url": None, "meta": None, "revised": [], "diff": None})
        pk = row.get("packins_no")
        if not pk:
            entry["error"] = "リンクなし"
            return entry
        try:
            xml_text, xml_url, used_pk, note = fetch_xml(pk)
            entry["xml_url"] = xml_url
            entry["note"] = note
            if used_pk != pk:
                entry["fetched_packins_no"] = used_pk
                entry["pack_url"] = f"{BASE}/go/pack/{used_pk}/"
                pk = used_pk
            if not xml_text:
                entry["error"] = "XMLが見つからない"
                return entry
            parsed = parse_packins_xml(xml_text)
            meta = parsed["meta"]
            entry["meta"] = meta
            if meta.get("company_id"):
                entry["pdf_url"] = f"{BASE}/go/pdf/{meta['company_id']}_{pk}"
            entry["revised"] = parsed["revised"]
            key = doc_key(pk)
            prev = self.load_archive(key)
            if prev:
                changes = diff_lines([tuple(x) for x in prev["lines"]], parsed["lines"])
                entry["diff"] = {
                    "prev_packins_no": prev["packins_no"],
                    "prev_fetched_at": prev["fetched_at"],
                    "prev_seen_date": prev.get("seen_date"),
                    "changes": changes,
                }
            self.save_archive(key, {
                "packins_no": pk, "fetched_at": entry["fetched_at"], "seen_date": date,
                "brand": row.get("brand"), "lines": parsed["lines"],
            })
        except Exception as e:  # noqa: BLE001
            entry["error"] = f"{type(e).__name__}: {e}"
        return entry

    def run(self, limit_days: int | None = None, dry: bool = False) -> dict:
        log("1week.html を取得中…")
        days = parse_week_page(http_get(WEEK_URL))
        log(f"ページ上の日付: {[d['date'] for d in days]}")
        if limit_days:
            days = days[:limit_days]
        summary = {"processed": [], "skipped": []}
        aborted = False
        # 古い日付から処理(差分の前後関係を正しくするため)
        for day in sorted(days, key=lambda d: d["date"]):
            date = day["date"]
            loaded = self.load_day(date)
            existed = loaded is not None
            stored = loaded or {"date": date, "updates": [], "deletes": [],
                                "created_at": now_jst()}
            # エラーだった行は次回やり直す(成功分だけ「取得済み」扱い)
            have = {u.get("packins_no") for u in stored["updates"] if u.get("packins_no") and not u.get("error")}
            have_del = {(d["brand"], d["company"]) for d in stored["deletes"]}
            todo = [u for u in day["updates"] if u.get("packins_no") and u["packins_no"] not in have]
            todo_del = [d for d in day["deletes"] if (d["brand"], d["company"]) not in have_del]
            if not todo and not todo_del:
                summary["skipped"].append(date)
                continue
            log(f"{date}: 新しい行 更新{len(todo)}件 / 削除{len(todo_del)}件 を処理")
            if dry:
                continue
            net_fails = 0    # 連続ネットワーク失敗数(サーバーに繋がらない状態で延々リトライしないため)
            stored_rows = 0  # 今回ちゃんと記録できた行数
            for i, row in enumerate(todo, 1):
                log(f"  ({i}/{len(todo)}) {row['brand']}")
                new_entry = self.process_update(row, date)
                err = new_entry.get("error") or ""
                if "GET failed" in err:
                    # 通信自体に失敗した行は記録に残さない(次回の実行で自動的にやり直される)
                    log(f"     ! {err}")
                    net_fails += 1
                    if net_fails >= 3:
                        log("!! ネットワーク不調が3件続いたため、この回はここで打ち切り(取得できた分だけ保存し、残りは次回の実行で取り込む)")
                        aborted = True
                        break
                    time.sleep(SLEEP)
                    continue
                net_fails = 0
                # 以前のエラー行があれば置き換え(同じ位置に)
                idx = next((k for k, u in enumerate(stored["updates"])
                            if u.get("packins_no") == row["packins_no"] and u.get("error")), None)
                if idx is None:
                    stored["updates"].append(new_entry)
                else:
                    stored["updates"][idx] = new_entry
                if err:
                    log(f"     ! {err}")
                stored_rows += 1
                time.sleep(SLEEP)
            for d in todo_del:
                stored["deletes"].append(d)
            if existed or stored_rows or todo_del:
                stored["updated_at"] = now_jst()
                stored["source"] = WEEK_URL
                self.save_day(stored)
                summary["processed"].append({"date": date, "updates": stored_rows, "deletes": len(todo_del)})
            else:
                # 1行も取れなかった新しい日は保存しない(空の日付ページを作らない)
                log(f"{date}: 取得できた行が無いため保存なし(次回の実行でやり直し)")
            if aborted:
                summary["aborted"] = True
                break
        return summary


if __name__ == "__main__":
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent
    w = Watcher(root)
    print(json.dumps(w.run(), ensure_ascii=False, indent=1))
