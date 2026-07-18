"""Tests for src/darkship_batch.py — 暗船 SAR 取證批次執行器。

全部合成：stdout 樣本以 src/fetch_sar_chip.py main() 實際 print 的格式
建構，餵給純函式（parse_fetch_output / build_todo / verdict），不碰
子行程、網路或 committed data 檔。
"""
import darkship_batch as d


def fresh_record():
    return {
        "product": None, "found": False, "length_m": None,
        "peak_ratio": None, "n_pixels": None, "saturated": False,
        "error": None,
    }


# ── parse_fetch_output：stdout 解析 ──────────────────────────────────────────

FOUND_STDOUT = (
    "🔎 查詢 2026-07-15 涵蓋 (23.5, 122.1) 的 IW GRDH 產品...\n"
    "   ✅ S1A_IW_GRDH_1SDV_20260715T215500_20260715T215525_059000_075000_ABCD.SAFE\n"
    "      成像: 2026-07-15T21:55:00.000Z\n"
    "📡 S3 視窗讀取: s1a-iw-grd-vv-20260715t215500-001.tiff\n"
    "🎯 亮目標: ~85.0 m、峰值 14.2× 海面背景（37 px）\n"
    "🖼  已存: chips/2026-07-15_23.5_122.1.png\n"
)


def test_parse_found_target():
    r = fresh_record()
    is_failure = d.parse_fetch_output(FOUND_STDOUT, 0, r)
    assert not is_failure
    assert r["found"] is True
    assert r["length_m"] == 85.0
    assert r["peak_ratio"] == 14.2
    assert r["n_pixels"] == 37
    assert r["saturated"] is False
    assert r["product"].startswith("S1A_IW_GRDH_") and r["product"].endswith(".SAFE")
    assert r["error"] is None


def test_parse_saturated():
    out = FOUND_STDOUT + "   ⚠️ 亮區超過上限 — 可能是陸地/大型結構，長度不可信\n"
    r = fresh_record()
    assert not d.parse_fetch_output(out, 0, r)
    assert r["found"] and r["saturated"]


def test_parse_no_target():
    out = ("   ✅ S1B_IW_GRDH_1SDV_X.SAFE\n"
           "⚪ 中心附近沒有超過門檻的亮目標 — 偵測可能是雜訊/低 RCS 目標\n"
           "🖼  已存: chips/x.png\n")
    r = fresh_record()
    assert not d.parse_fetch_output(out, 0, r)
    assert r["found"] is False
    assert r["error"] is None


def test_parse_clean_error():
    out = "❌ 該日期查無涵蓋此點的 Sentinel-1 GRD 產品（可能當天沒過境）\n"
    r = fresh_record()
    assert d.parse_fetch_output(out, 1, r)
    assert r["error"].startswith("❌")


def test_parse_crash_without_error_mark():
    out = "Traceback (most recent call last):\n  ...\nBotoCoreError: something\n"
    r = fresh_record()
    assert d.parse_fetch_output(out, 1, r)
    assert r["error"].startswith("crash:")
    assert "BotoCoreError" in r["error"]


def test_parse_unrecognized_output_is_failure():
    r = fresh_record()
    assert d.parse_fetch_output("something unexpected\n", 0, r)
    assert r["error"] == "unrecognized output"


# ── verdict：判定規則 ────────────────────────────────────────────────────────

def test_verdict_rules():
    assert d.verdict({"error": "x"}) == "❌ 失敗"
    assert d.verdict({"saturated": True, "found": True}) == "⚠️ 疑似陸地/固定結構"
    assert d.verdict({"found": True, "peak_ratio": 14.0}) == "✅ 確認實體目標"
    assert d.verdict({"found": True, "peak_ratio": 6.0}) == "🟡 弱目標"
    assert d.verdict({"found": False}) == "⚪ 無目標（雜訊或低RCS）"


# ── build_todo：篩選 / 去重 / 排序 / 上限 ────────────────────────────────────

def tgt(date, lat=23.0, lon=122.0, covered=True):
    return {"date": date, "lat": lat, "lon": lon, "covered": covered}


def test_build_todo_filters_uncovered_and_done():
    targets = [
        tgt("2026-07-10"),
        tgt("2026-07-12", covered=False),          # 無影像涵蓋 → 排除
        tgt("2026-07-14", lat=24.0),
        tgt("2026-07-16", lat=25.0),
    ]
    done = {d.result_key(tgt("2026-07-14", lat=24.0))}  # 已完成 → 跳過
    todo = d.build_todo(targets, done, limit=10)
    assert [t["date"] for t in todo] == ["2026-07-16", "2026-07-10"]  # 新→舊


def test_build_todo_limit_and_unlimited():
    targets = [tgt(f"2026-07-{day:02d}", lat=20 + day) for day in range(1, 9)]
    assert len(d.build_todo(targets, set(), limit=3)) == 3
    assert len(d.build_todo(targets, set(), limit=0)) == 8  # 0 = 全部


def test_result_key_matches_between_target_and_record():
    t = tgt("2026-07-15", lat=23.51, lon=122.13)
    record = {"date": "2026-07-15", "lat": 23.51, "lon": 122.13, "found": True}
    assert d.result_key(t) == d.result_key(record)
