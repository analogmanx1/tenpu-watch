# -*- coding: utf-8 -*-
"""毎日の実行入口: 取得 → 保存 → サイト生成
使い方:  python src/run.py            (リポジトリ直下を対象)
         python src/run.py --days 1   (最新1日だけ。テスト用)
         python src/run.py --build-only
         python src/run.py --build-only --if-index   (インタビューフォーム一覧をPMDAから取り直してサイト生成。数分かかる。毎日は回さない)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pmda_watch  # noqa: E402
import build_site  # noqa: E402
import if_index  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(Path(__file__).resolve().parent.parent))
    ap.add_argument("--days", type=int, default=None, help="ページ上の新しい方からN日だけ処理(テスト用)")
    ap.add_argument("--build-only", action="store_true", help="添付文書の取得はせずサイト生成だけ")
    ap.add_argument("--if-index", action="store_true", help="インタビューフォーム一覧(data/if_index.json)をPMDAから取り直す")
    a = ap.parse_args()
    root = Path(a.root)
    if a.if_index:
        meta = if_index.refresh(root)
        print(json.dumps({k: meta[k] for k in ("fetched_at", "count", "requests", "warnings")}, ensure_ascii=False))
    if not a.build_only:
        w = pmda_watch.Watcher(root)
        summary = w.run(limit_days=a.days)
        print(json.dumps(summary, ensure_ascii=False))
    build_site.build(root)
    return 0


if __name__ == "__main__":
    sys.exit(main())
