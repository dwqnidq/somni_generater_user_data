"""
统计每个用户文件中每条记录的 score 在总排名中的位次。

用法:
    python3 scripts/score_ranking.py [--top N] [--user UID] [--output FILE]

默认: 遍历 output/ 下所有 {uid}_somni_sleep_analysis.json，
      与 output/somni_sleep_analysis.json 做排名对比。
"""

import json
import os
import argparse
from bisect import bisect_right

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "output")
MAIN_FILE = os.path.join(OUTPUT_DIR, "somni_sleep_analysis.json")


def load_scores(path):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        data = [data]
    return data


def build_rank_lookup(all_scores_sorted):
    """返回一个函数: score -> 排名(从1开始, 分数越高排名越靠前)"""
    # all_scores_sorted 是升序排列
    def get_rank(score):
        # bisect_right 返回的是 score 在升序数组中应插入的位置
        # 排名 = 总数 - 插入位置 + 1 (分数越高排名越靠前)
        pos = bisect_right(all_scores_sorted, score)
        rank = len(all_scores_sorted) - pos + 1
        return min(rank, len(all_scores_sorted))  # 防止 score 低于最小值溢出
    return get_rank


def main():
    parser = argparse.ArgumentParser(description="查看用户 score 在总排名中的位次")
    parser.add_argument("--top", type=int, default=0, help="只显示排名前 N 的记录 (0=全部)")
    parser.add_argument("--user", type=str, default="", help="只处理指定 UID 的用户文件")
    parser.add_argument("--output", type=str, default="", help="将结果输出为 JSON 文件")
    args = parser.parse_args()

    # 1. 加载主文件, 提取所有 score
    print(f"加载主文件: {MAIN_FILE}")
    main_data = load_scores(MAIN_FILE)
    all_scores = [entry["score"] for entry in main_data if "score" in entry]
    all_scores_sorted = sorted(all_scores)
    total = len(all_scores_sorted)
    print(f"主文件共 {total} 条记录, score 范围: {all_scores_sorted[0]} ~ {all_scores_sorted[-1]}")

    get_rank = build_rank_lookup(all_scores_sorted)

    # 2. 遍历用户文件
    if args.user:
        user_files = [os.path.join(OUTPUT_DIR, f"{args.user}_somni_sleep_analysis.json")]
    else:
        user_files = sorted([
            os.path.join(OUTPUT_DIR, f)
            for f in os.listdir(OUTPUT_DIR)
            if f.endswith("_somni_sleep_analysis.json")
        ])

    print(f"共找到 {len(user_files)} 个用户文件\n")

    all_results = []

    for uf_path in user_files:
        uid = os.path.basename(uf_path).replace("_somni_sleep_analysis.json", "")
        entries = load_scores(uf_path)

        user_results = []
        for entry in entries:
            score = entry.get("score")
            if score is None:
                continue
            rank = get_rank(score)
            percentile = round((1 - (rank - 1) / total) * 100, 2)
            user_results.append({
                "stats_date": entry.get("stats_date", "N/A"),
                "score": score,
                "rank": rank,
                "total": total,
                "percentile": percentile,
            })

        # 排名越小越好, 按 rank 升序排
        user_results.sort(key=lambda x: x["rank"])

        avg_score = round(sum(r["score"] for r in user_results) / len(user_results), 1) if user_results else 0
        avg_rank = round(sum(r["rank"] for r in user_results) / len(user_results), 1) if user_results else 0
        best = user_results[0] if user_results else None
        worst = user_results[-1] if user_results else None

        summary = {
            "uid": uid,
            "record_count": len(user_results),
            "avg_score": avg_score,
            "avg_rank": avg_rank,
            "best_rank": best["rank"] if best else None,
            "best_date": best["stats_date"] if best else None,
            "best_score": best["score"] if best else None,
            "worst_rank": worst["rank"] if worst else None,
            "worst_date": worst["stats_date"] if worst else None,
            "worst_score": worst["score"] if worst else None,
            "details": user_results,
        }

        # 如果指定了 top, 过滤
        if args.top > 0:
            summary["details"] = [r for r in user_results if r["rank"] <= args.top]

        all_results.append(summary)

        # 打印摘要
        print(f"用户: {uid}")
        print(f"  记录数: {len(user_results)}, 平均分: {avg_score}, 平均排名: {avg_rank}/{total}")
        if best:
            print(f"  最佳: {best['stats_date']} score={best['score']} 排名={best['rank']}/{total} (Top {best['percentile']}%)")
        if worst:
            print(f"  最差: {worst['stats_date']} score={worst['score']} 排名={worst['rank']}/{total} (Top {worst['percentile']}%)")

        if args.top > 0:
            top_entries = [r for r in user_results if r["rank"] <= args.top]
            print(f"  Top {args.top} 内的记录: {len(top_entries)} 条")
            for r in top_entries:
                print(f"    {r['stats_date']} score={r['score']} 排名={r['rank']}/{total} (Top {r['percentile']}%)")
        print()

    # 3. 输出文件
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(all_results, f, ensure_ascii=False, indent=2)
        print(f"结果已写入: {args.output}")


if __name__ == "__main__":
    main()
