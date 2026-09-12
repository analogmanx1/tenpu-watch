# -*- coding: utf-8 -*-
"""ファーマビットのアイコン(ホーム画面用・タブ用)を描いて docs/assets/ に書き出す。
   python scripts/make_icons.py     (Pillow が必要: pip install pillow)

デザイン: サイトの青(style.css の --acc と同じ #2a62b8)の角丸四角に、白と水色の2色カプセルを斜めに1本。
色・カプセルの大きさ・角の丸さは下の定数で変えられる(変えたら再実行 → commit → push)。

出力(サイズは各OSの決まり):
  icon-32.png            … ブラウザのタブ用(favicon)
  icon-192.png / icon-512.png … Android のホーム画面用(manifest.webmanifest から参照)
  icon-512-maskable.png  … Android の「丸く切り抜く」端末用(余白を多めにした全面塗り)
  apple-touch-icon.png   … iPhone/iPad のホーム画面用(180px・全面塗り。角丸はiOSが付ける)
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

BG = (42, 98, 184)            # 背景の青(--acc)
CAP_A = (255, 255, 255)       # カプセルの片側(白)
CAP_B = (176, 208, 255)       # カプセルのもう片側(水色)
CORNER = 0.22                 # 角丸の半径(1辺に対する割合)。0=四角
CAP_LEN = 0.66                # カプセルの長さ(1辺に対する割合)
CAP_W = 0.27                  # カプセルの太さ(同上)
ANGLE = 45                    # カプセルの傾き(度)
SS = 4                        # なめらかにするための拡大倍率(内部処理)

OUT = Path(__file__).resolve().parent.parent / "docs" / "assets"


def draw(size: int, rounded: bool, scale: float = 1.0) -> Image.Image:
    """size: 出力の1辺(px)。rounded: 角を透明にして丸めるか(False=全面塗り)。scale: カプセルの縮小率(maskable用)"""
    s = size * SS
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if rounded:
        d.rounded_rectangle([0, 0, s - 1, s - 1], radius=int(s * CORNER), fill=BG)
    else:
        d.rectangle([0, 0, s - 1, s - 1], fill=BG)

    # カプセルは別レイヤーに水平に描いてから回転して重ねる
    cap = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    cd = ImageDraw.Draw(cap)
    L, W = s * CAP_LEN * scale, s * CAP_W * scale
    cx = cy = s / 2
    box = [cx - L / 2, cy - W / 2, cx + L / 2, cy + W / 2]
    cd.rounded_rectangle(box, radius=int(W / 2), fill=CAP_A + (255,))
    right = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    ImageDraw.Draw(right).rounded_rectangle(box, radius=int(W / 2), fill=CAP_B + (255,))
    mask = Image.new("L", (s, s), 0)
    ImageDraw.Draw(mask).rectangle([cx, 0, s, s], fill=255)
    cap.paste(right, (0, 0), mask)
    cd.line([(cx, box[1]), (cx, box[3])], fill=BG + (255,), width=max(2, s // 64))   # 真ん中の区切り線
    cap = cap.rotate(ANGLE, resample=Image.BICUBIC, center=(cx, cy))
    img.alpha_composite(cap)
    return img.resize((size, size), Image.LANCZOS)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    jobs = [("icon-32.png", 32, True, 1.0),
            ("icon-192.png", 192, True, 1.0),
            ("icon-512.png", 512, True, 1.0),
            ("icon-512-maskable.png", 512, False, 0.78),
            ("apple-touch-icon.png", 180, False, 0.9)]
    for name, size, rounded, scale in jobs:
        im = draw(size, rounded, scale)
        if not rounded:
            im = im.convert("RGB")   # 全面塗りは透明なしで保存(iOSは透明があると黒くなる)
        im.save(OUT / name, optimize=True)
        print(f"{name}: {size}x{size}")


if __name__ == "__main__":
    main()
