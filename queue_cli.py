"""Read-only Google Sheets publishing queue inspection for GitHub Actions.

Never publishes, approves, retries, or edits spreadsheet rows.
"""
import argparse
import json
from collections import Counter
from google_sheets_bridge import healthcheck, plan_content_queue


def summarize(items):
    counts=Counter(item["state"] for item in items)
    targets=Counter()
    for item in items:
        for target in item.get("targets", []):
            targets[target["state"]]+=1
    return {"total_tasks":len(items),"task_states":dict(sorted(counts.items())),
            "platform_decisions":dict(sorted(targets.items())),
            "tasks":items}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--healthcheck",action="store_true")
    parser.add_argument("--plan",action="store_true")
    args=parser.parse_args()
    if not args.healthcheck and not args.plan:
        parser.error("Specify --healthcheck or --plan")
    result={}
    if args.healthcheck:
        result["sheets"]=healthcheck()
    if args.plan:
        result["queue"]=summarize(plan_content_queue())
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=="__main__":
    main()
