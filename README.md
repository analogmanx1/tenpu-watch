# 添付文書ウォッチ / 薬剤師ツールボックス

PMDA「過去1週間以内に更新された添付文書情報」を毎朝7時に自動で取り込み、
**どの添付文書の・どこが変わったか**を自分専用のWebサイト(GitHub Pages)に掲載する仕組み。
バックナンバーと検索つき。あとから計算機などのツールも同じサイトに足せる。

## 仕組み(ざっくり)
```
毎朝7時(GitHub Actions) ─→ 1week.html を取得
  ─→ まだ記録していない日付・行を拾う(1週間分あるので取りこぼしに強い)
  ─→ 各添付文書のXMLを取得 → 「今回改訂箇所(＊＊印)」を抽出
  ─→ 前回保存した本文があれば差分(前→後)も作成
  ─→ data/ に保存、docs/ にサイト生成 → コミット → GitHub Pages に公開
```

## フォルダ
| 場所 | 中身 | 手で触る? |
|---|---|---|
| `src/pmda_watch.py` | 取得・XML解析・差分 | ロジック変更時のみ |
| `src/build_site.py` | サイト生成(デザインもここ) | 見た目を変えたいとき |
| `src/run.py` | 実行入口 | - |
| `data/days/YYYY-MM-DD.json` | 日ごとの記録(自動) | ✕ |
| `archive/docs/*.json` | 各添付文書の最新本文(差分用・自動) | ✕ |
| `docs/` | 公開サイト本体 | `tools/` だけOK |
| `docs/tools/` | 計算機などの手作りページ(置くだけで一覧に載る) | ◯ 読み方は `docs/tools/README.md` |
| `.github/workflows/daily.yml` | 毎朝の自動実行設定 | 時刻変更など |

## 手元で動かす(テスト)
```bash
python src/run.py --days 1      # 最新1日だけ取り込み+サイト生成
python src/run.py --build-only  # サイト生成だけ
python src/run.py               # 本番と同じ(ページ上の全日付)
```
生成された `docs/index.html` をブラウザで開けば確認できる。

## GitHub 側の初期設定(1回だけ)
1. リポジトリを作って push
2. Settings → Pages → Build and deployment → Source を **GitHub Actions** にする
3. Actions タブ → `daily-pmda-watch` → Run workflow で1回手動実行 → 公開URLが出る

## 注意・制限
- 「変更点」は (a) 企業がXMLに付けた「今回改訂」マーク と (b) 前回保存分との本文差分 の2本立て。
  初めて見る添付文書は (b) が出ない(次の更新から出る)。版を変えずに再掲載されたものは (a) が過去の改訂箇所になることがある
- PMDAのページは1週間分しか無いので、自動実行が8日以上止まると取りこぼす(止まったらActionsタブを確認)
- PMDAサーバーに負荷をかけないよう、1件ごとに少し待ち時間を入れている(1日分で数十秒〜数分)
- 個人用。PMDA・各社の一次情報(PMDAページ/PDFリンク)を必ず確認すること
