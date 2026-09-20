# -*- coding: utf-8 -*-
"""
添付文書XMLの「PC内の写し」(キャッシュ)。
PMDAから1回取ったXMLを、このPCのローカルディスクに圧縮して置いておく。
→ 「◯◯のとき注意する薬」のようなテーマを後から足したり、拾い方を変えたりしても、
  約1.1万件をPMDAへ取り直しに行かずに済む(3時間 → 数分)。

- 置き場: %LOCALAPPDATA%\\tenpu-watch\\xmlcache\\(薬効3桁)\\(PDFコード).xml.gz
  (Googleドライブ・gitの外。同期詰まりを起こさないため。環境変数 TENPU_XML_CACHE で変更可)
- PCごとに別々に持つ。無いPCでは普通にPMDAへ取りに行って、そのとき保存する
- 改版でPDFコードが変わった古い写しは prune() で掃除する
- 個人利用の作業用の写しで、サイトには出さない(公開するのは抜き出した文と出典リンクだけ)
"""
from __future__ import annotations

import gzip
import os
import re
from pathlib import Path

_SAFE = re.compile(r"^[A-Za-z0-9_]{5,80}$")


def cache_dir() -> Path:
    env = os.environ.get("TENPU_XML_CACHE")
    if env:
        return Path(env)
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".cache")
    return Path(base) / "tenpu-watch" / "xmlcache"


def _path(u: str) -> Path | None:
    if not _SAFE.match(u or "") or "_" not in u:
        return None
    return cache_dir() / u.split("_")[1][:3] / f"{u}.xml.gz"


def get(u: str) -> str | None:
    p = _path(u)
    if not p or not p.exists():
        return None
    try:
        return gzip.decompress(p.read_bytes()).decode("utf-8", "replace")
    except (OSError, EOFError, gzip.BadGzipFile):
        return None   # 壊れた写しは無いものとして扱う(次の取得で上書きされる)


def put(u: str, xml_text: str) -> bool:
    p = _path(u)
    if not p or not xml_text:
        return False
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_bytes(gzip.compress(xml_text.encode("utf-8"), 6))
        tmp.replace(p)
        return True
    except OSError:
        return False   # 保存できなくても本体の処理は止めない(次回また取りに行くだけ)


def prune(valid: set[str]) -> int:
    """いまの添付文書一覧に無いPDFコード(改版前の古い写し)を消す。消した数を返す"""
    root = cache_dir()
    n = 0
    if not root.exists():
        return 0
    for f in root.glob("*/*.xml.gz"):
        if f.name[:-7] not in valid:
            try:
                f.unlink()
                n += 1
            except OSError:
                pass
    return n


def stats() -> tuple[int, int]:
    """(件数, 合計バイト)"""
    root = cache_dir()
    if not root.exists():
        return 0, 0
    files = list(root.glob("*/*.xml.gz"))
    return len(files), sum(f.stat().st_size for f in files)
