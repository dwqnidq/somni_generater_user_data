"""文件作用：仅对 language 为 en 的 sleep event 刷新 _id，并修正「AI active intervention」的 related_event_id。"""



import argparse

import glob

import json

import os

from typing import Dict, List



from bson import ObjectId



TARGET_LANGUAGE = "en"

TARGET_EVENT_TYPE = "AI active intervention"





def _collect_targets(input_path: str) -> List[str]:

    if os.path.isfile(input_path):

        return [input_path]

    if os.path.isdir(input_path):

        return sorted(glob.glob(os.path.join(input_path, "*_sleep_events.json")))

    return sorted(glob.glob(input_path))





def refresh_ids_for_file(file_path: str) -> Dict[str, int]:

    with open(file_path, "r", encoding="utf-8") as f:

        data = json.load(f)



    if not isinstance(data, list):

        return {"events": 0, "new_ids": 0, "ai_related_updated": 0}



    new_ids = 0

    ai_related_updated = 0



    for i, event in enumerate(data):

        if not isinstance(event, dict):

            continue

        if event.get("language") != TARGET_LANGUAGE:

            continue



        event["_id"] = str(ObjectId())

        new_ids += 1



        if event.get("event_type") != TARGET_EVENT_TYPE:

            continue



        if i > 0 and isinstance(data[i - 1], dict):

            event["related_event_id"] = data[i - 1]["_id"]

            ai_related_updated += 1

        else:

            event["related_event_id"] = ""



    with open(file_path, "w", encoding="utf-8") as f:

        json.dump(data, f, ensure_ascii=False, indent=2)



    return {

        "events": len(data),

        "new_ids": new_ids,

        "ai_related_updated": ai_related_updated,

    }





def main() -> None:

    parser = argparse.ArgumentParser(

        description=(

            "遍历 sleep_events：仅处理 language=en 的记录——写入新的 _id；若 event_type 为 "

            "「AI active intervention」，则将 related_event_id 设为列表中上一条（索引减一）的 _id；"

            "其它 language 不修改。"

        )

    )

    parser.add_argument(

        "--input",

        default=os.path.join(os.path.dirname(__file__), "..", "output"),

        help="文件/目录/通配符；默认处理 output 目录下 *_sleep_events.json",

    )

    args = parser.parse_args()



    input_path = os.path.abspath(args.input)

    targets = _collect_targets(input_path)

    if not targets:

        print(f"未找到目标文件: {input_path}")

        return



    total_events = 0

    total_new_ids = 0

    total_ai_related = 0

    for fp in targets:

        stat = refresh_ids_for_file(fp)

        total_events += stat["events"]

        total_new_ids += stat["new_ids"]

        total_ai_related += stat["ai_related_updated"]

        print(

            f"已处理 {os.path.basename(fp)}: "

            f"events={stat['events']}, "

            f"新_id条数(en)={stat['new_ids']}, "

            f"{TARGET_EVENT_TYPE}_related_event_id更新={stat['ai_related_updated']}"

        )



    print(

        f"完成，共处理文件 {len(targets)} 个，"

        f"列表项 {total_events} 条，写入新 _id（仅 en）{total_new_ids} 条，"

        f"{TARGET_EVENT_TYPE} 更新 related_event_id {total_ai_related} 条。"

    )





if __name__ == "__main__":

    main()


