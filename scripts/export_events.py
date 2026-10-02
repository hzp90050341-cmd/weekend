"""把 ArtifactData 匯出的活動 JSON 檔合併成網站讀取的 events.json。

用法：python scripts/export_events.py <匯出資料夾>
匯出資料夾裡要有 events/*.json（每筆活動一個檔）與 meta/status.json，
也就是 ArtifactData list/get 加 out_dir 產生的結構。
過期活動（依台北日期）與 hidden 的活動不會輸出。
"""
import json, sys, pathlib
from datetime import datetime, timedelta, timezone

src = pathlib.Path(sys.argv[1])
out = pathlib.Path(__file__).resolve().parent.parent / "events.json"
today = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")

events = []
for f in sorted((src / "events").glob("*.json")):
    e = json.loads(f.read_text(encoding="utf-8"))
    e["id"] = f.stem
    end = e.get("end_date") or e.get("start_date") or e.get("review_by") or "9999-12-31"
    if e.get("hidden") or end < today:
        continue
    events.append(e)

meta_file = src / "meta" / "status.json"
meta = json.loads(meta_file.read_text(encoding="utf-8")) if meta_file.exists() else {}
meta.pop("unreachable", None)  # 內部維護資訊，不公開

out.write_text(json.dumps({"generated_at": today, "meta": meta, "events": events},
                          ensure_ascii=False, indent=1), encoding="utf-8")
print(f"wrote {len(events)} events to {out}")
