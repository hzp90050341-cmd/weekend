"""把 Artifact 版網頁（沒有 <html>/<head> 外框）包成 Netlify 用的完整 index.html。

用法：python scripts/build_index.py <Artifact 版網頁路徑>
"""
import sys, pathlib

body = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
head = (
    '<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">'
    '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
    '<meta name="description" content="全台展覽、市集、百貨優惠與節慶活動，依縣市鄉鎮分類，每天早上自動更新。">'
    '<style>:root{color-scheme:light;padding-top:env(safe-area-inset-top,0px);padding-bottom:env(safe-area-inset-bottom,0px)}'
    'body{margin:0}img{max-width:100%}[hidden]{display:none!important}</style>'
    '</head><body>'
)
out = pathlib.Path(__file__).resolve().parent.parent / "index.html"
out.write_text(head + body + "</body></html>", encoding="utf-8")
print(f"wrote {out}")
