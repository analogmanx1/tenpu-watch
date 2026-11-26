# -*- coding: utf-8 -*-
"""機能タイルのドット絵アイコン(16×16マス)を描いて docs/assets/tiles/ に書き出す。
   python scripts/make_tile_icons.py           (Pillow が必要: pip install pillow)
   python scripts/make_tile_icons.py --sheet   (確認用に、全部を8倍に拡大して並べた一覧も scratch に出す)

2026-11-26 指示: タイルの絵文字をドット絵にする。AI生成ではなく、ここに1マスずつ書いた設計図から作る(無料・マス目がそろう)。
・下の ICONS の1文字=1マス。文字と色の対応は PALETTE。「.」は透明
・ファイル名(キー)は、機能ページのフォルダ名(watch, tenpu …)か、手作りツールのファイル名(days.html → days)
・build_site.py の feature_tiles は、このフォルダに同じ名前のPNGがあればドット絵、無ければ絵文字を出す
・表示の大きさは CSS の --pix(何倍に拡大するか。整数にするとマス目がにじまない)
・色を変えたいときは PALETTE、形を変えたいときは ICONS の設計図を直して再実行 → サイト生成
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

OUT = Path(__file__).resolve().parent.parent / "docs" / "assets" / "tiles"

PALETTE = {
    "k": (58, 50, 71),      # 輪郭(こげ茶がかった濃い紫)
    "w": (255, 255, 255),   # 白
    "l": (214, 220, 230),   # うすいグレー(紙の折り返し・影)
    "s": (160, 170, 186),   # グレー
    "b": (110, 160, 224),   # 青(文字の線など)
    "c": (170, 225, 245),   # 水色(レンズ)
    "r": (232, 84, 76),     # 赤
    "p": (255, 150, 190),   # ピンク
    "y": (255, 210, 70),    # 黄
    "o": (245, 160, 60),    # オレンジ
    "g": (110, 196, 110),   # 緑
    "v": (180, 140, 224),   # 紫
    "n": (168, 116, 79),    # 茶
    "R": (176, 48, 52),     # 濃い赤(本の背・しずくの影)
    "u": (80, 130, 215),    # 青(IFの本の表紙)
    "B": (45, 85, 165),     # 濃い青(本の背)
}

ICONS: dict[str, list[str]] = {
    # 添付文書更新情報: 紙の文書+右上に赤い「お知らせの丸」(スマホの通知の丸のイメージ)
    "watch": [
        "..........kkkk..",
        ".........krrrrk.",
        ".kkkkkkkkrrwrrrk",
        ".kwwwwwwkrrrrrrk",
        ".kwbbbbwkrrrrrrk",
        ".kwwwwwwwkrrrrk.",
        ".kwwwwwwwwkkkk..",
        ".kwwwwwwwwwk....",
        ".kwbbbbbbbwk....",
        ".kwwwwwwwwwk....",
        ".kwbbbbbbbwk....",
        ".kwwwwwwwwwk....",
        ".kwbbbbbwwwk....",
        ".kwwwwwwwwwk....",
        ".kwwwwwwwwwk....",
        ".kkkkkkkkkkk....",
    ],
    # 識別コード検索: 虫めがねのレンズの中に、白とピンクのカプセル
    "shikibetsu": [
        "....kkkkk.......",
        "..kkcccccckk....",
        ".kcwwccccccck...",
        "kcwcccccccccck..",
        "kcccccccccccck..",
        "kcckkkkkkkccck..",
        "kckwwwwpppkcck..",
        "kckwwwwpppkcck..",
        "kcckkkkkkkccck..",
        "kcccccccccccck..",
        ".kcccccccccckk..",
        "..kkcccccckknk..",
        "....kkkkkkknnnk.",
        "...........knnnk",
        "............knnk",
        ".............kk.",
    ],
    # 日数計算: 赤い見出しのカレンダー+オレンジの丸(この日!)
    "days": [
        "....kk....kk....",
        "..kkkkkkkkkkkk..",
        "..krrkrrrrkrrk..",
        "..krrrrrrrrrrk..",
        "..krrrrrrrrrrk..",
        "..kkkkkkkkkkkk..",
        "..kwwwwwwwwwwk..",
        "..kwswswswswwk..",
        "..kwwwwwwwwwwk..",
        "..kwswswsoowwk..",
        "..kwwwwwwoowwk..",
        "..kwswswswswwk..",
        "..kwwwwwwwwwwk..",
        "..kwswswswswwk..",
        "..kwwwwwwwwwwk..",
        "..kkkkkkkkkkkk..",
    ],
    # 添付文書検索: 赤い本(表紙に白いラベル)
    "tenpu": [
        "................",
        "..kkkkkkkkkkkk..",
        "..kRrrrrrrrrrk..",
        "..kRrkkkkkkrrk..",
        "..kRrkwwwwkrrk..",
        "..kRrkwsswkrrk..",
        "..kRrkwwwwkrrk..",
        "..kRrkkkkkkrrk..",
        "..kRrrrrrrrrrk..",
        "..kRrrrrrrrrrk..",
        "..kRrrrrrrrrrk..",
        "..kRrrrrrrrrrk..",
        "..kRkkkkkkkkkk..",
        "..kRkwwwwwwwwk..",
        "..kkkkkkkkkkkk..",
        "................",
    ],
    # インタビューフォーム検索: 青い本(表紙に白で「IF」)
    "if": [
        "................",
        "..kkkkkkkkkkkk..",
        "..kBuuuuuuuuuk..",
        "..kBuuuuuuuuuk..",
        "..kBuuwuwwwuuk..",
        "..kBuuwuwuuuuk..",
        "..kBuuwuwwuuuk..",
        "..kBuuwuwuuuuk..",
        "..kBuuwuwuuuuk..",
        "..kBuuuuuuuuuk..",
        "..kBuuuuuuuuuk..",
        "..kBuuuuuuuuuk..",
        "..kBkkkkkkkkkk..",
        "..kBkwwwwwwwwk..",
        "..kkkkkkkkkkkk..",
        "................",
    ],
    # 副作用検索: 白い板に3本の棒グラフ(頻度の高い順)
    "fukusayo": [
        "................",
        ".kkkkkkkkkkkkkk.",
        ".kwwwwwwwwwwwwk.",
        ".kwwwwwwwwwwwwk.",
        ".kwwwwwppwwwwwk.",
        ".kwwwwwppwwwwwk.",
        ".kwwwwwppwwwwwk.",
        ".kwwwwwppwwbbwk.",
        ".kwwwwwppwwbbwk.",
        ".kwggwwppwwbbwk.",
        ".kwggwwppwwbbwk.",
        ".kwggwwppwwbbwk.",
        ".kwggwwppwwbbwk.",
        ".kwsssssssssswk.",
        ".kkkkkkkkkkkkkk.",
        "................",
    ],
    # 薬理作用検索: DNAのらせん(ピンクと青の2本+はしごの段)
    "yakuri": [
        "....ppssssbb....",
        "....pp....bb....",
        ".....ppssbb.....",
        "......ppbb......",
        "......bbpp......",
        ".....bb..pp.....",
        "....bbsssspp....",
        "....bb....pp....",
        "....bbsssspp....",
        ".....bb..pp.....",
        "......bbpp......",
        "......ppbb......",
        ".....ppssbb.....",
        "....pp....bb....",
        "....ppssssbb....",
        ".....pp..bb.....",
    ],
    # 透析投薬ガイドライン検索: 赤い血のしずく
    "touseki": [
        "................",
        ".......kk.......",
        "......krrk......",
        "......krrk......",
        ".....krrrrk.....",
        ".....krrrrk.....",
        "....krrrrrrk....",
        "...krrrrrrrrk...",
        "...krwrrrrrrk...",
        "..krwrrrrrrrrk..",
        "..krwrrrrrrrrk..",
        "..krrrrrrrrrrk..",
        "..krrrrrrrrrRk..",
        "...krrrrrrrRk...",
        "....kkRRRRkk....",
        "......kkkk......",
    ],
    # 術前休薬・禁忌チェック: 赤十字の看板がついた病院
    "shujutsu": [
        ".....kkkkkk.....",
        ".....kwrrwk.....",
        ".....krrrrk.....",
        ".....kwrrwk.....",
        "..kkkkkkkkkkkk..",
        "..kllllllllllk..",
        "..klbblbblbblk..",
        "..klbblbblbblk..",
        "..kllllllllllk..",
        "..klbblbblbblk..",
        "..klbblbblbblk..",
        "..kllllllllllk..",
        "..klbblnnlbblk..",
        "..klbblnnlbblk..",
        "..kkkkkkkkkkkk..",
        "................",
    ],
    # 造影剤チェック: 横向きの注射器(水色の薬液)
    "zouei": [
        "................",
        "................",
        "................",
        "kk..............",
        "kk.kkkkkkkkkk...",
        "kk.kwwwcccccck..",
        "kk.kwswcscscck..",
        "kkkkwwwcccccckss",
        "kkkkwwwcccccckss",
        "kk.kwswcscscck..",
        "kk.kwwwcccccck..",
        "kk.kkkkkkkkkk...",
        "kk..............",
        "................",
        "................",
        "................",
    ],
    # 蓄積率計算: 方眼の板に、上がって平らになる赤い線(オレンジの点線=定常状態)
    "accumulation": [
        "................",
        ".kkkkkkkkkkkkkk.",
        ".kwwwlwwwlwwwwk.",
        ".kwowowowowowwk.",
        ".kwwwlwwwlrrrwk.",
        ".kllllllrrrrrlk.",
        ".kwwwlwrrrwwwwk.",
        ".kwwwlrrwlwwwwk.",
        ".kwwwrrwwlwwwwk.",
        ".klllrllllllllk.",
        ".kwwrlwwwlwwwwk.",
        ".kwwrlwwwlwwwwk.",
        ".kwrwlwwwlwwwwk.",
        ".kwrwlwwwlwwwwk.",
        ".kkkkkkkkkkkkkk.",
        "................",
    ],
    # 血液透析による薬物除去率: 緑の液が入った試験管(泡つき)
    "dialysis": [
        "................",
        "....kkkkkkkk....",
        ".....kwwwwk.....",
        ".....kwwwwk.....",
        ".....kwwwwk.....",
        ".....kwwwwk.....",
        ".....kwwwwk.....",
        ".....kwgggk.....",
        ".....kwgggk.....",
        ".....kwgwgk.....",
        ".....kwgggk.....",
        ".....kwggwk.....",
        ".....kwgggk.....",
        "......kggk......",
        ".......kk.......",
        "................",
    ],
    # ガンマ計算: 点滴のバッグ(ラベルつき)と管
    "gamma": [
        "......kkkk......",
        "......k..k......",
        "...kkkkkkkkkk...",
        "...kwwwwwwwwk...",
        "...kwwwwwwwwk...",
        "...kcccccccck...",
        "...kcwwwwwwck...",
        "...kcwbbbbwck...",
        "...kcwwwwwwck...",
        "...kcccccccck...",
        "...kcccccccck...",
        "....kcccccck....",
        ".....kkcckk.....",
        "......kcck......",
        ".......kk.......",
        ".......kk.......",
    ],
}


def render(rows: list[str]) -> Image.Image:
    assert len(rows) == 16 and all(len(r) == 16 for r in rows), "設計図は16×16マス"
    im = Image.new("RGBA", (16, 16), (0, 0, 0, 0))
    for y, row in enumerate(rows):
        for x, ch in enumerate(row):
            if ch != ".":
                im.putpixel((x, y), PALETTE[ch] + (255,))
    return im


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    ims = {}
    for key, rows in ICONS.items():
        ims[key] = render(rows)
        ims[key].save(OUT / f"{key}.png", optimize=True)
        print("wrote", OUT / f"{key}.png")
    if "--sheet" in sys.argv:   # 確認用: 8倍に拡大して横に並べる(背景は薄いグレー)
        z = 8
        sheet = Image.new("RGBA", (len(ims) * (16 * z + 16) + 16, 16 * z + 32), (236, 238, 242, 255))
        for i, im in enumerate(ims.values()):
            sheet.alpha_composite(im.resize((16 * z, 16 * z), Image.NEAREST), (16 + i * (16 * z + 16), 16))
        dest = Path(sys.argv[sys.argv.index("--sheet") + 1]) if len(sys.argv) > sys.argv.index("--sheet") + 1 else OUT / "_sheet.png"
        sheet.save(dest)
        print("sheet", dest)


if __name__ == "__main__":
    main()
