# -*- coding: utf-8 -*-
"""毎日の実行入口: 取得 → 保存 → サイト生成
使い方:  python src/run.py            (リポジトリ直下を対象)
         python src/run.py --days 1   (最新1日だけ。テスト用)
         python src/run.py --build-only
         python src/run.py --build-only --if-index --tenpu-index
             (インタビューフォーム/添付文書の一覧をPMDAから取り直してサイト生成。
              IFは数分・添付文書は10〜20分かかる。毎日0:15の自動実行と同じ)
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
import shikibetsu_index  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(Path(__file__).resolve().parent.parent))
    ap.add_argument("--days", type=int, default=None, help="ページ上の新しい方からN日だけ処理(テスト用)")
    ap.add_argument("--build-only", action="store_true", help="添付文書の取得はせずサイト生成だけ")
    ap.add_argument("--if-index", action="store_true", help="インタビューフォーム一覧(data/if_index.json)をPMDAから取り直す")
    ap.add_argument("--tenpu-index", action="store_true", help="添付文書一覧(data/tenpu_index.json)をPMDAから取り直す")
    ap.add_argument("--shikibetsu", action="store_true",
                    help="識別コード一覧(data/shikibetsu_index.json)を差分更新(最大300件/回。自宅PC専用)")
    a = ap.parse_args()
    root = Path(a.root)
    if a.shikibetsu:
        # 失敗しても添付文書ウォッチ本体(取得・コミット)は止めない
        try:
            meta = shikibetsu_index.refresh(root, max_docs=300)
            print(json.dumps({k: meta.get(k) for k in ("updated_at", "with_codes", "codes", "pending")}, ensure_ascii=False))
        except Exception as e:  # noqa: BLE001
            print(f"!! 識別コード一覧の更新に失敗(今回はスキップ。次回やり直し): {e}")
    if a.if_index:
        meta = if_index.refresh(root)
        print(json.dumps({k: meta[k] for k in ("fetched_at", "count", "requests", "warnings")}, ensure_ascii=False))
    if a.tenpu_index:
        meta = if_index.refresh(root, doc="tenpu")
        print(json.dumps({k: meta[k] for k in ("fetched_at", "count", "requests", "warnings")}, ensure_ascii=False))
    if not a.build_only:
        w = pmda_watch.Watcher(root)
        summary = w.run(limit_days=a.days)
        print(json.dumps(summary, ensure_ascii=False))
    build_site.build(root)
    return 0


if __name__ == "__main__":
    sys.exit(main())
