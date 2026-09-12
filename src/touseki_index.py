# -*- coding: utf-8 -*-
"""
白鷺病院 薬剤科「透析患者に対する投薬ガイドライン」(CKD患者に関する薬剤情報 Database)の
五十音順索引(ア行〜ワ行の44ページ)から「商品名 → PDFのURL」の一覧を集めて data/touseki_index.json に保存する。
「透析投薬ガイドライン検索」ページ(docs/touseki/)の元データ。

方針:
  - PDF本体はこのリポジトリに保存しない(白鷺病院サイトの「著作権について」で無断複写・転用NGのため)。
    一覧(リンク集)だけを自前で持ち、クリックで白鷺病院のPDFをそのまま開く(同サイトの案内は「リンクは自由」)
  - 白鷺病院内の採用区分マーク(▼◎○△など。他施設には関係ないと手引きに明記)は表示から外す
  - 索引は商品名だけなので、PMDA添付文書一覧(data/tenpu_index.json)と突き合わせて一般名を補い、
    一般名でも検索できるようにする(商品名の前方一致で、候補の一般名が2種類以下のときだけ採用=誤結合を避ける)
  - 索引ページのどれかが取れなかったら古い一覧を残す(部分的な一覧で上書きしない)

使い方:  python src/touseki_index.py              (リポジトリ直下の data/touseki_index.json を更新)
         python src/run.py --build-only --touseki-index   (同上 + サイト生成)
白鷺病院サイトへのアクセスは 46回(索引44 + 入口・手引き)・1秒待ちつき。
"""
from __future__ import annotations

import bisect
import html
import json
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

JST = timezone(timedelta(hours=9))
UA = "pmda-tenpu-watch/1.0 (personal use; polite crawler)"
SLEEP = 1.0   # サーバーにやさしく(秒)

SITE = "https://www.shirasagi-hp.or.jp"
GATE_URL = SITE + "/goda/fmly/gate.html"            # ガイドラインの入口ページ(版の表記がある)
TEBIKI_URL = SITE + "/goda/fmly/tebiki.html"        # 利用の手引き(データベース更新日の表記がある)
REFS_URL = SITE + "/goda/fmly/references.html"      # 引用文献
INDEX_TOP_URL = SITE + "/goda/fmly/pdf/index.html"  # 五十音順索引(商品名のみ)
INDEX_URL = SITE + "/goda/fmly/pdf/index/index-{key}.html"
PDF_BASE = SITE + "/goda/fmly/pdf/files/"           # PDFは …/files/番号.pdf

# 索引ページ(44枚)。key はページ名の綴り、名前は表示用
ROWS = [("a", "ア"), ("i", "イ"), ("u", "ウ"), ("e", "エ"), ("o", "オ"),
        ("ka", "カ"), ("ki", "キ"), ("ku", "ク"), ("ke", "ケ"), ("ko", "コ"),
        ("sa", "サ"), ("si", "シ"), ("su", "ス"), ("se", "セ"), ("so", "ソ"),
        ("ta", "タ"), ("ti", "チ"), ("tu", "ツ"), ("te", "テ"), ("to", "ト"),
        ("na", "ナ"), ("ni", "ニ"), ("nu", "ヌ"), ("ne", "ネ"), ("no", "ノ"),
        ("ha", "ハ"), ("hi", "ヒ"), ("hu", "フ"), ("he", "ヘ"), ("ho", "ホ"),
        ("ma", "マ"), ("mi", "ミ"), ("mu", "ム"), ("me", "メ"), ("mo", "モ"),
        ("ya", "ヤ"), ("yu", "ユ"), ("yo", "ヨ"),
        ("ra", "ラ"), ("ri", "リ"), ("ru", "ル"), ("re", "レ"), ("ro", "ロ"),
        ("wa", "ワ")]
ROW_NAMES = {k: f"{n}行" for k, n in ROWS}
# 検索ページの「白鷺病院の索引で探す」ボタン用: 先頭のカナ → 索引ページ(濁点・小書きはJS側で基本形に直してから引く)
KANA_ROW = {n: k for k, n in ROWS}
KANA_ROW.update({"ヲ": "wa", "ン": "wa"})

ADOPTION_MARKS = "▼◎○△●×▽"   # 白鷺病院内の採用区分(▼非購入 ◎常時採用 ○用時採用 △院外のみ など)


def log(msg: str) -> None:
    print(f"[{datetime.now(JST).strftime('%H:%M:%S')}] {msg}", flush=True)


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        raw = r.read()
    m = re.search(rb'charset=["\']?([\w-]+)', raw[:4000], re.I)
    enc = m.group(1).decode("ascii", "ignore") if m else "utf-8"
    try:
        return raw.decode(enc)
    except (LookupError, UnicodeDecodeError):
        return raw.decode("utf-8", errors="replace")


# ---------------------------------------------------------------- 索引ページの読み取り
def parse_index(page: str) -> list[tuple[str, str]]:
    """索引ページ1枚 → [(PDF番号, 表示ラベル)]。本文は <section class="contents"> の中に
    <a href="../files/747.pdf"> ◎アーチスト錠，○カルベジロール錠 ［内］</a><br /> が並んでいる"""
    m = re.search(r'<section class="contents[^"]*">(.*?)</section>', page, re.S)
    body = m.group(1) if m else page
    out = []
    for href, text in re.findall(r'<a\s+href="([^"]+)"[^>]*>(.*?)</a>', body, re.S):
        mm = re.search(r"files/(\d+)\.pdf", href)
        if not mm:
            continue
        label = html.unescape(re.sub(r"<[^>]+>", "", text))
        label = clean_label(label)
        if label:
            out.append((mm.group(1), label))
    return out


def clean_label(label: str) -> str:
    """採用区分マークを外し、カッコ・空白の書き方をそろえる(表示用)。
    例: '◎アーチスト錠，○カルベジロール錠 ［内］' → 'アーチスト錠，カルベジロール錠 ［内］'"""
    s = label.replace("　", " ").replace("･", "・").replace("｢", "「").replace("｣", "」")
    s = re.sub(rf"[{ADOPTION_MARKS}]", "", s)
    s = re.sub(r"[\[［]\s*([^\[\]［］]{1,8}?)\s*[\]］]", r"［\1］", s)   # [内] / ［内] / [内］ → ［内］
    s = re.sub(r"\s*([，,・、])\s*", r"\1", s)                       # 区切りの前後の空白を詰める
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"\s*(［)", r" \1", s)                               # ［内］ の前は半角スペース1つ
    s = re.sub(r"[?？\s]+$", "", s)                                 # 元ページの末尾に付いている「？」は落とす
    return s.strip()


NOTE_RE = re.compile(r"[＜<][^＞>]*[＞>]|[（(][^）)]*(?:中止|未販売)[^）)]*[）)]")
BRACKET_RE = re.compile(r"［[^］]*］")


def split_names(label: str) -> list[str]:
    """ラベル → 商品名の候補(前方一致・一般名の突き合わせ用)。
    ［内］などの区分・＜販売中止＞などの注記を除き、「，」「・」「、」で分ける(「/」は「250単位/mL」のように名前の一部なので分けない)"""
    s = NOTE_RE.sub("", label)
    s = BRACKET_RE.sub(" ", s)
    parts = [p.strip(" 　") for p in re.split(r"[，,・、]", s)]
    return [p for p in parts if p]


# ---------------------------------------------------------------- 一般名の補完(PMDA添付文書一覧との突き合わせ)
def norm(s: str) -> str:
    """ひらがな→カタカナ、全角→半角(NFKC)、小文字、空白除去(検索ページのJSと同じ規則)"""
    s = unicodedata.normalize("NFKC", s or "").lower()
    s = "".join(chr(ord(c) + 0x60) if "ぁ" <= c <= "ゖ" else c for c in s)
    return re.sub(r"\s+", "", s)


HYPHENS = "-‐‑‒–—―−－"


def mnorm(s: str) -> str:
    """突き合わせ用の正規化: norm + カナに挟まれたハイフンは長音「ー」扱い(メチコバ－ル等の表記ゆれ)、
    残りのハイフン・中点は除く(アスパラ-CA錠 / ネオラミン・スリービー液 の書き分けを吸収)"""
    s = norm(s)
    s = re.sub(rf"(?<=[ァ-ヶ])[{HYPHENS}](?=[ァ-ヶ])", "ー", s)
    return re.sub(rf"[{HYPHENS}・･]", "", s)


def _cls(c: str) -> str:
    """文字の種類: k=カタカナ・長音, j=漢字, a=英数字, p=その他(記号・カッコ)"""
    if "ァ" <= c <= "ヺ" or c == "ー":
        return "k"
    if "一" <= c <= "鿿" or c in "々〆":
        return "j"
    if c.isascii() and c.isalnum():
        return "a"
    return "p"


# 剤形・容器・デバイスなどの語(正規化後の表記)。商品名の候補を「この語の手前」で切る/この語だけの断片は捨てる
FORM_WORDS = sorted("""
錠 カプセル 散 末 細粒 顆粒 ドライシロップ ds シロップ 内用液 内服液 経口液 経口ゼリー ゼリー 液 注 静注 点滴 皮下注 筋注 注射 注入 注腸 浣腸
軟膏 クリーム ローション ゲル フォーム スプレー テープ パップ ハップ パッチ 貼付 坐剤 坐薬 膣錠 腟錠 膣坐剤 腟坐剤 点眼 点鼻 点耳 眼軟膏 吸入
エアゾール エアロゾル インヘラー エリプタ ディスカス レスピマット ジェヌエア ブリーズヘラー タービュヘイラー ツイストヘラー スイングヘラー エアロスフィア
フレックスペン フレックスタッチ ミリオペン ソロスター イノレット ペンフィル オートインジェクター アテオス シリンジ キット バッグ バイアル アンプル ポリアンプ プレフィルド
配合 原末 透析剤 輸液 補正液 消毒液 綿球 水性懸濁 懸濁 糖衣 徐放 腸溶 口腔用 舌下 チュアブル 発泡 含嗽 うがい 外用 経皮 局注 眼科用 耳科用
静注用 筋注用 皮下注用 注射用 点滴用 吸入用 od cr la sr er hi sd bs 用
""".split(), key=len, reverse=True)
FORM_RE = re.compile("|".join(re.escape(w) for w in FORM_WORDS))
UNIT_RE = re.compile(r"\d+(?:\.\d+)?|mg|mcg|μg|ml|meq|iu|cm|mm|w/v|v/v|%|単位|号|倍|[gl]\b")
KANA_FORM_WORDS = [w for w in FORM_WORDS if _cls(w[0]) == "k"]


def is_form_only(p: str) -> bool:
    """「点滴静注用」「クリーム」「DS76%」「mLシリンジ」のように、剤形・数量だけで商品名を含まない断片か"""
    rest = FORM_RE.sub("", p)
    rest = UNIT_RE.sub("", rest)
    rest = re.sub(r"[^\wァ-ヶー一-龥]", "", rest)
    return len(rest) < 2


def candidates(p: str) -> list[str]:
    """商品名(正規化済み) → 前方一致に使う候補を長い順に。
    全体 → 文字種が変わる位置(カナ→漢字/数字/記号)や カナの剤形語の手前 で切った prefix。
    例: アクテムラ皮下注シリンジ → [全体, アクテムラ皮下注, アクテムラ]"""
    cuts = set()
    for i in range(1, len(p)):
        if _cls(p[i - 1]) != _cls(p[i]):
            cuts.add(i)
    for w in KANA_FORM_WORDS:
        j = p.find(w, 1)
        while j > 0:
            cuts.add(j)
            j = p.find(w, j + 1)
    out = [p]
    for i in sorted(cuts, reverse=True):
        c = p[:i].rstrip("(「")
        if len(c) >= GenericLookup.MIN_LEN and c not in out:
            out.append(c)
    return out


class GenericLookup:
    """販売名の前方一致で一般名を引く。data/tenpu_index.json の販売名(「／」区切りで複数)をならべて二分探索"""
    MIN_LEN = 4        # これより短い前方一致は当てにしない(3文字だと「アルミ」→別薬のような誤結合が出た)
    MAX_GENERICS = 2   # 候補の一般名がこれより多ければ「曖昧」として採用しない

    def __init__(self, tenpu_items: list[dict]):
        pairs = set()
        for it in tenpu_items:
            g = (it.get("g") or "").strip()
            if not g:
                continue
            for brand in re.split(r"[／/]", it.get("n") or ""):
                b = mnorm(brand)
                if b:
                    pairs.add((b, g))
        self.keys = sorted(pairs)
        self.names = [k for k, _ in self.keys]

    def _match(self, prefix: str) -> set[str]:
        i = bisect.bisect_left(self.names, prefix)
        found = set()
        while i < len(self.names) and self.names[i].startswith(prefix):
            found.add(self.keys[i][1])
            i += 1
        return found

    def find(self, product: str) -> list[str]:
        p = mnorm(product)
        if len(p) < self.MIN_LEN or is_form_only(p):
            return []
        for c in candidates(p):
            found = self._match(c)
            if found:
                # 一致が曖昧(一般名が3種類以上)なら、それより短い候補はもっと曖昧なので打ち切り
                return sorted(found) if len(found) <= self.MAX_GENERICS else []
        return []


# ---------------------------------------------------------------- 一覧の作成
def build_items(pages: dict[str, list[tuple[str, str]]], lookup: GenericLookup | None) -> list[dict]:
    """行ごとの [(PDF番号, ラベル)] → 重複(同じPDFが複数の行に載る)をまとめた一覧
    項目: i=PDF番号, n=表示ラベル, s=商品名の候補, r=載っている行(key), g=一般名(補完できたものだけ)"""
    by_id: dict[str, dict] = {}
    order: list[str] = []
    for key, _ in ROWS:
        for pdf_id, label in pages.get(key, []):
            it = by_id.get(pdf_id)
            if it is None:
                it = {"i": pdf_id, "n": label, "s": split_names(label), "r": []}
                by_id[pdf_id] = it
                order.append(pdf_id)
            elif label != it["n"]:
                for nm in split_names(label):
                    if nm not in it["s"]:
                        it["s"].append(nm)
            if key not in it["r"]:
                it["r"].append(key)
    items = [by_id[k] for k in order]
    if lookup:
        for it in items:
            gs: list[str] = []
            for nm in it["s"]:
                for g in lookup.find(nm):
                    if g not in gs:
                        gs.append(g)
            if gs:
                it["g"] = gs
    return items


def load_tenpu_items(root: Path) -> list[dict]:
    f = root / "data" / "tenpu_index.json"
    if not f.exists():
        return []
    try:
        return json.loads(f.read_text(encoding="utf-8")).get("items") or []
    except Exception as e:  # noqa: BLE001
        log(f"!! data/tenpu_index.json を読めませんでした({e})。一般名の補完なしで続けます")
        return []


def refresh(root: Path) -> dict:
    """白鷺病院の索引44ページを取り直して data/touseki_index.json を書く。meta を返す。
    1ページでも取れなければ例外(古い一覧はそのまま残る)"""
    out = root / "data" / "touseki_index.json"
    warnings: list[str] = []
    requests = 0

    def get(url: str) -> str:
        nonlocal requests
        requests += 1
        try:
            return fetch(url)
        finally:
            time.sleep(SLEEP)

    edition = ""
    db_updated = ""
    try:
        m = re.search(r"(\d+(?:st|nd|rd|th)\s*Edition)", get(GATE_URL), re.I)
        edition = m.group(1) if m else ""
    except Exception as e:  # noqa: BLE001
        warnings.append(f"入口ページ取得失敗: {e}")
    try:
        m = re.search(r"データベースは\s*(\d{4}年\d{1,2}月\d{1,2}日)\s*に更新", get(TEBIKI_URL))
        db_updated = m.group(1) if m else ""
    except Exception as e:  # noqa: BLE001
        warnings.append(f"利用の手引き取得失敗: {e}")

    pages: dict[str, list[tuple[str, str]]] = {}
    for key, name in ROWS:
        page = get(INDEX_URL.format(key=key))   # 失敗はそのまま例外にして中断(部分的な一覧で上書きしない)
        rows = parse_index(page)
        if not rows:
            raise RuntimeError(f"{name}行の索引ページに薬剤リンクが見つかりません(ページ構造が変わった?): {INDEX_URL.format(key=key)}")
        pages[key] = rows
        log(f"{name}行 {len(rows)}件")

    lookup = GenericLookup(load_tenpu_items(root))
    items = build_items(pages, lookup if lookup.keys else None)
    n_generic = sum(1 for it in items if it.get("g"))
    meta = {
        "fetched_at": datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S"),
        "source": GATE_URL,
        "edition": edition,
        "db_updated": db_updated,
        "count": len(items),
        "links": sum(len(v) for v in pages.values()),
        "with_generic": n_generic,
        "requests": requests,
        "warnings": warnings,
        "rows": ROW_NAMES,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"meta": meta, "items": items}, ensure_ascii=False, indent=0), encoding="utf-8", newline="\n")
    log(f"data/touseki_index.json: {len(items)}件(リンク{meta['links']}件・一般名つき{n_generic}件)"
        f" {edition} DB更新 {db_updated or '不明'}")
    return meta


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(Path(__file__).resolve().parent.parent))
    a = ap.parse_args()
    meta = refresh(Path(a.root))
    print(json.dumps({k: meta[k] for k in ("fetched_at", "count", "with_generic", "requests", "warnings")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
