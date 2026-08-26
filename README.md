# 添付文書ウォッチ / 薬剤師ツールボックス

PMDA「過去1週間以内に更新された添付文書情報」を毎日自動で取り込み、
**どの添付文書の・どこが変わったか**を自分専用のWebサイト(GitHub Pages)に掲載する仕組み。
バックナンバーと検索つき。あとから計算機などのツールも同じサイトに足せる。
**インタビューフォーム検索**(薬剤名を入れる → 候補をクリック → IFのPDFが開く)も同じサイトにある。

## 仕組み(ざっくり)
```
毎日 6:30・17:30・PC起動時(自宅PCのタスク) ─→ 1week.html を取得
  ─→ まだ記録していない日付・行を拾う(1週間分あるので取りこぼしに強い)
  ─→ 各添付文書のXMLを取得 → 「今回改訂箇所(＊＊印)」を抽出
  ─→ 前回保存した本文があれば差分(前→後)も作成
  ─→ data/ に保存、docs/ にサイト生成 → コミット → GitHub Pages に公開
```

## サイトの階層
```
トップ(ホーム)  docs/index.html        ← 入口。項目は site/home.json で管理(1項目=1カード)
 └ 💊 薬剤師ツールボックス  docs/toolbox/   ← 添付文書ウォッチのまとめ + ツール一覧
     ├ 📄 添付文書ウォッチ  docs/watch/     ← 日別ページ・バックナンバー・検索(自動生成)
     ├ 📘 インタビューフォーム検索 docs/if/ ← 薬剤名→候補→クリックでIF(PDF)。データは data/if_index.json(毎日0:15自動更新)
     └ 🧮 ツール            docs/tools/     ← 計算機など(HTMLを置くだけで自動掲載)
```
トップに別の機能を足すときは `site/home.json` の `sections` に1つ追加:
```json
{"emoji": "🎮", "title": "○○", "desc": "説明", "href": "xxx/index.html"}
```
(`href` は docs/ からの相対パス。`live: "toolbox"` を付けると最新の更新状況が1行出る)

## フォルダ
| 場所 | 中身 | 手で触る? |
|---|---|---|
| `src/pmda_watch.py` | 取得・XML解析・差分 | ロジック変更時のみ |
| `src/build_site.py` | サイト生成(デザインもここ) | 見た目を変えたいとき |
| `src/if_index.py` | インタビューフォーム一覧をPMDAから集める(毎日0:15に自動実行) | ロジック変更時のみ |
| `src/run.py` | 実行入口 | - |
| `data/days/YYYY-MM-DD.json` | 日ごとの記録(自動) | ✕ |
| `data/if_index.json` | インタビューフォーム一覧(販売名・一般名・企業・PDFリンク)。`--if-index` で作り直す | ✕(コマンドで更新) |
| `archive/docs/*.json` | 各添付文書の最新本文(差分用・自動) | ✕ |
| `site/home.json` | トップページの項目一覧 | ◯ |
| `docs/` | 公開サイト本体 | `tools/` と自分で作ったフォルダだけOK |
| `docs/tools/` | 計算機などの手作りページ(置くだけで一覧に載る) | ◯ 読み方は `docs/tools/README.md` |
| `scripts/local_update.ps1` | 自宅PCからの取り込み本体(タスクスケジューラが呼ぶ) | ロジック変更時のみ |
| `scripts/register_task.ps1` | PCへのタスク登録(PCごとに1回実行) | - |
| `.github/workflows/daily.yml` | クラウド側の「ビルド+公開」(取り込み・コミットはしない。公開の保険) | 時刻変更など |
| `.github/workflows/if-index.yml` | インタビューフォーム一覧の毎日更新(0:15 JST)+手動ボタン | 時刻変更など |

## 手元で動かす(テスト)
```bash
python src/run.py --days 1      # 最新1日だけ取り込み+サイト生成
python src/run.py --build-only  # サイト生成だけ
python src/run.py               # 本番と同じ(ページ上の全日付)
```
生成された `docs/index.html` をブラウザで開けば確認できる。

## インタビューフォーム検索(docs/if/)
- 使い方: 検索ページで薬剤名(一般名・販売名)や企業名を入力 → 候補をクリックするとIFのPDFが別タブで開く。
  スペース区切りでAND絞り込み。ひらがな/カタカナ・全角/半角の違いは自動で吸収
- 「PMDAで最新を検索 ↗」ボタンは、入力した薬剤名で **IFだけにチェック済みのPMDA検索** を別タブで開く(一覧が古いときの保険)
- 一覧データ(`data/if_index.json`)は **毎日 0:15 JST に自動で取り直す**(`.github/workflows/if-index.yml`)。すぐ取り直したいとき:
  - GitHub: Actions タブ → `if-index` → Run workflow(数分で再公開される)
  - 手元: `python src/run.py --build-only --if-index` → commit → push
- 仕組み: PMDAの検索は1回1000件までなので、薬効分類(大分類36)ごとに検索し、1000件を超えた分類だけ小分類に分けて取り直す。
  1秒待ちつきで150〜200回アクセス、数分で終わる

## GitHub 側の初期設定(1回だけ)
1. リポジトリを作って push
2. Settings → Pages → Build and deployment → Source を **GitHub Actions** にする
3. Actions タブ → `daily-pmda-watch` → Run workflow で1回手動実行 → 公開URLが出る

(設定済み: https://github.com/analogmanx1/tenpu-watch → 公開URL https://analogmanx1.github.io/tenpu-watch/ 。
`src/` `site/` `docs/tools/` を編集して push すると自動で再公開される)

## 注意・制限
- 「変更点」は (a) 企業がXMLに付けた「今回改訂」マーク と (b) 前回保存分との本文差分 の2本立て。
  初めて見る添付文書は (b) が出ない(次の更新から出る)。版を変えずに再掲載されたものは (a) が過去の改訂箇所になることがある
- PMDAのページは1週間分しか無いので、自動実行が8日以上止まると取りこぼす(止まったらActionsタブを確認)
- **PMDAは添付文書の個別ページ(/go/pack)を海外・クラウドIPからブロックしている**(2026-08-24確認。1週間一覧の静的ページは取れる)。
  そのため取り込みは自宅PCのタスクスケジューラ(`tenpu-watch-auto`: 毎日6:30・17:30・PC起動3分後、`scripts/local_update.ps1`)で行う。
  PCが数日オフでも、PMDAのページに1週間分あるので7日以内にどこかで起動すれば取りこぼさない。別PCでも使うときは `scripts/register_task.ps1` を1回実行
- GitHub Actionsは2本: 毎日 7:00と17:00 `daily-pmda-watch`(**ビルド+公開のみ**。取り込み・コミットはしない=2026-08-26変更。以前はActionsも取り込みコミットしていて、PC側タスクとほぼ同時刻のコミットでマージ衝突→自動更新の全停止を起こしたため、書き手はPC側の1系統に絞った)と 毎日0:15 `if-index`(インタビューフォーム一覧の取得+コミット。PMDAの検索サイト側なのでクラウドから取れる)。同時には走らない設定(concurrency)
- PC側タスクの入口はローカルディスクのランチャー(`%LOCALAPPDATA%	enpu-watch\launch.ps1`、register_task.ps1 が自動生成)。Googleドライブのマウント前にタスクが起動しても、ランチャーがマウントを待ってから本体を呼ぶ
- インタビューフォームは改版でPDFのURLが変わることがある。開けないときは候補の「PMDA詳細 ↗」か「PMDAで最新を検索 ↗」で最新を確認し、気になれば一覧を取り直す
- PMDAサーバーに負荷をかけないよう、1件ごとに少し待ち時間を入れている(1日分で数十秒〜数分)
- 個人用。PMDA・各社の一次情報(PMDAページ/PDFリンク)を必ず確認すること

## 手元で編集→公開の流れ(毎回)
毎日ボットが GitHub 側にコミットするので、**編集を始める前に必ず取り込む**:
```bash
git pull --no-rebase origin main
```
編集・確認(`python src/run.py --build-only`)したら:
```bash
git add -A && git commit -m "変更内容" && git push
```
docs/ の自動生成ファイルで衝突したら、`python src/run.py --build-only` で作り直して `git add -A` すればOK。
※ Googleドライブ上のgitはまれに rebase が変になる。その時は `git rebase --abort` → `git reset --soft origin/main` → 再ビルド → commit → push で復旧
