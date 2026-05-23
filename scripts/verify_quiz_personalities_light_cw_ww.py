#!/usr/bin/env python3
"""校验 quiz_personalities cw/ww 与 description K 是否符合 k值.md 规则。"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv
from pymongo import MongoClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.fix_quiz_personalities_light_cw_ww import (  # noqa: E402
    EXCLUDED_MHR_NAMES,
    K_TO_CW_WW,
    LANGUAGE,
    base_query,
    lookup_cw_ww,
    parse_kelvin_from_description,
    resolve_kelvin_and_description,
)

BACKUP = PROJECT_ROOT / "output/quiz_personalities_mhr4_zh_before_cw_ww_fix.json"


def _extract_db_name_from_uri(uri: str, fallback: str = "Fullive") -> str:
    try:
        parsed = urlparse(uri)
        db_name = (parsed.path or "").lstrip("/")
        if db_name:
            return db_name.split("?")[0]
    except Exception:
        pass
    return fallback


def _codes_key(codes: list) -> str:
    return "-".join(str(c) for c in codes)


def check_periods(periods, doc_key: str) -> list[str]:
    errors: list[str] = []
    if not isinstance(periods, list):
        return errors

    for pi, period in enumerate(periods):
        if not isinstance(period, dict):
            continue
        phase = period.get("phase") or pi
        schemes = period.get("schemes")
        if not isinstance(schemes, list):
            continue
        for si, scheme in enumerate(schemes):
            if not isinstance(scheme, dict):
                continue
            name = scheme.get("name") or si
            light = scheme.get("light")
            if not isinstance(light, dict):
                continue
            desc = light.get("description", "")
            effective_k, _, _ = resolve_kelvin_and_description(desc)
            expected = lookup_cw_ww(effective_k) if effective_k else None
            stops = light.get("color_stops")
            if not isinstance(stops, list):
                continue

            if effective_k is None:
                # 应跳过：cw/ww 不应被脚本改成表内值（允许原样 0/0 或其它）
                if expected is None and stops:
                    for sti, stop in enumerate(stops):
                        if not isinstance(stop, dict):
                            continue
                        k_parsed = parse_kelvin_from_description(desc)
                        if k_parsed is not None:
                            exp = lookup_cw_ww(k_parsed)
                            if exp:
                                cw, ww = int(stop.get("cw") or 0), int(stop.get("ww") or 0)
                                if (cw, ww) == exp:
                                    errors.append(
                                        f"{doc_key} {phase}/{name} stop{sti}: "
                                        f"无K文案但 cw/ww={cw}/{ww} 像已写入表值"
                                    )
                continue

            if expected is None:
                errors.append(
                    f"{doc_key} {phase}/{name}: K={effective_k} 不在表但 description={desc!r}"
                )
                continue

            exp_cw, exp_ww = expected
            for sti, stop in enumerate(stops):
                if not isinstance(stop, dict):
                    continue
                cw = int(stop.get("cw") or 0)
                ww = int(stop.get("ww") or 0)
                if (cw, ww) != (exp_cw, exp_ww):
                    errors.append(
                        f"{doc_key} {phase}/{name} stop{sti}: "
                        f"desc={desc!r} 期望 cw/ww={exp_cw}/{exp_ww} 实际 {cw}/{ww}"
                    )

            raw_k = parse_kelvin_from_description(desc)
            if raw_k is not None and raw_k < 2700:
                errors.append(
                    f"{doc_key} {phase}/{name}: description 仍含 K<2700: {raw_k} ({desc!r})"
                )
    return errors


def compare_backup_to_current(backup_docs: list, current_by_id: dict) -> list[str]:
    diffs: list[str] = []
    allowed_prefixes = ("periods.",)

    for bdoc in backup_docs:
        oid = str(bdoc.get("_id"))
        cdoc = current_by_id.get(oid)
        if not cdoc:
            diffs.append(f"备份 _id={oid} 在当前库不存在")
            continue
        key = _codes_key(bdoc.get("mhr_codes") or [])

        b_periods = json.dumps(bdoc.get("periods"), sort_keys=True, default=str)
        c_periods = json.dumps(cdoc.get("periods"), sort_keys=True, default=str)
        if b_periods == c_periods:
            continue

        # 粗查：除 periods 外其它顶层字段不应变
        for field in bdoc:
            if field in ("_id", "periods"):
                continue
            if json.dumps(bdoc.get(field), sort_keys=True, default=str) != json.dumps(
                cdoc.get(field), sort_keys=True, default=str
            ):
                diffs.append(f"{key}: 非 periods 字段被改动: {field}")

        # description 只允许 2500/2200/2000 -> 2700
        b_per = bdoc.get("periods") or []
        c_per = cdoc.get("periods") or []
        for pi, (bp, cp) in enumerate(zip(b_per, c_per)):
            for si, (bs, cs) in enumerate(
                zip(bp.get("schemes") or [], cp.get("schemes") or [])
            ):
                bl = (bs or {}).get("light") or {}
                cl = (cs or {}).get("light") or {}
                bd, cd = bl.get("description"), cl.get("description")
                if bd == cd:
                    continue
                bk = parse_kelvin_from_description(bd)
                ck = parse_kelvin_from_description(cd)
                if bk is not None and bk < 2700 and ck == 2700:
                    if (bd or "").replace(str(bk), "2700", 1) != (cd or ""):
                        # 更严：仅首位 K 变化
                        from scripts.fix_quiz_personalities_light_cw_ww import (
                            replace_leading_kelvin_only,
                        )

                        if replace_leading_kelvin_only(str(bd), 2700) != str(cd):
                            diffs.append(
                                f"{key} p{pi}s{si}: description 变化超出 K 抬高: "
                                f"{bd!r} -> {cd!r}"
                            )
                elif bd != cd:
                    diffs.append(
                        f"{key} p{pi}s{si}: description 非预期变化: {bd!r} -> {cd!r}"
                    )

                for sti, (bst, cst) in enumerate(
                    zip(bl.get("color_stops") or [], cl.get("color_stops") or [])
                ):
                    if not isinstance(bst, dict) or not isinstance(cst, dict):
                        continue
                    for k in set(bst) | set(cst):
                        if k in ("cw", "ww"):
                            continue
                        if bst.get(k) != cst.get(k):
                            diffs.append(
                                f"{key} p{pi}s{si} stop{sti}: 非 cw/ww 字段 {k} 被改动"
                            )
    return diffs


def main() -> None:
    os.chdir(PROJECT_ROOT)
    load_dotenv(PROJECT_ROOT / ".env")
    uri = os.getenv("MONGODB_URI") or os.getenv("MONGO_URI")
    if not uri:
        print("缺少 MONGODB_URI", file=sys.stderr)
        sys.exit(1)
    db_name = os.getenv("MONGODB_DB") or _extract_db_name_from_uri(uri)

    client = MongoClient(uri, serverSelectionTimeoutMS=8000)
    try:
        docs = list(client[db_name]["quiz_personalities"].find(base_query()))
    finally:
        client.close()

    print(f"当前库: {len(docs)} 条 (language=zh, mhr4, 非测试)\n")

    all_errors: list[str] = []
    ok_schemes = 0
    skip_schemes = 0
    low_k_remaining = 0

    for doc in docs:
        key = _codes_key(doc.get("mhr_codes") or [])
        periods = doc.get("periods")
        if not isinstance(periods, list):
            continue
        for period in periods:
            for scheme in period.get("schemes") or []:
                light = (scheme or {}).get("light") or {}
                desc = light.get("description", "")
                k = parse_kelvin_from_description(desc)
                if k is None:
                    skip_schemes += 1
                elif k < 2700:
                    low_k_remaining += 1
                else:
                    exp = lookup_cw_ww(k)
                    if exp:
                        ok_schemes += 1
        all_errors.extend(check_periods(periods, key))

    print("=== 1. cw/ww 与 description K 一致性 ===")
    if all_errors:
        print(f"失败 {len(all_errors)} 项:")
        for e in all_errors[:30]:
            print(f"  {e}")
        if len(all_errors) > 30:
            print(f"  ... 另有 {len(all_errors) - 30} 项")
    else:
        print("通过：所有可解析 K 的 scheme，各 color_stop 的 cw/ww 均与 k值.md 一致")

    print(f"\n可解析 K 的 scheme（应已对齐）: {ok_schemes}")
    print(f"仍无 K（灯光关闭等，跳过）: {skip_schemes}")
    print(f"description 仍 K<2700: {low_k_remaining}")

    print("\n=== 2. 与写库前备份对比 ===")
    if not BACKUP.exists():
        print(f"备份不存在: {BACKUP}，跳过对比")
    else:
        backup_docs = json.loads(BACKUP.read_text(encoding="utf-8"))
        current_by_id = {str(d["_id"]): d for d in docs}
        diffs = compare_backup_to_current(backup_docs, current_by_id)
        if diffs:
            print(f"差异/异常 {len(diffs)} 项:")
            for d in diffs[:25]:
                print(f"  {d}")
            if len(diffs) > 25:
                print(f"  ... 另有 {len(diffs) - 25} 项")
        else:
            print("通过：仅 periods 内 description(K抬高) 与 cw/ww 有变化，其它字段与 color_stop 其它键未动")

    # 抽样
    print("\n=== 3. 抽样（K 曾 <2700 的 relax/入睡）===")
    samples = 0
    for doc in docs:
        key = _codes_key(doc.get("mhr_codes") or [])
        for period in doc.get("periods") or []:
            for scheme in period.get("schemes") or []:
                light = (scheme or {}).get("light") or {}
                desc = str(light.get("description") or "")
                if desc.startswith("2700") and ("暖黄光" in desc or "深暖光" in desc):
                    stop = (light.get("color_stops") or [{}])[0]
                    print(
                        f"  {key} {(scheme or {}).get('name')}: {desc!r} "
                        f"cw/ww={stop.get('cw')}/{stop.get('ww')}"
                    )
                    samples += 1
                    if samples >= 4:
                        break
            if samples >= 4:
                break
        if samples >= 4:
            break

    sys.exit(1 if all_errors or (BACKUP.exists() and diffs) else 0)


if __name__ == "__main__":
    main()
