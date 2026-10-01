"""부하 측정 뒤 Prometheus 지표를 JSON으로 남긴다. 표준 라이브러리만 쓴다.

서버의 진행 중 요청과 점유한 자리가 둘 다 0이 될 때까지 기다린 뒤, 한 번 더 수집될 시간을
두고 조회한다. Task.cancel()로 요청 처리는 끝났지만 스레드 작업이 남는 경우가 있어, 진행 중
요청만 보면 작업이 남은 채 조회할 수 있다.

사용(측정한 앱과 Prometheus가 떠 있는 상태에서):
    python3 load/snapshot_prometheus.py --start 2026-09-27T01:00:00Z --out <파일>
"""

import argparse
import json
import time
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

PROMETHEUS = "http://localhost:9090/api/v1/"
SCRAPE_SECONDS = 5
IDLE = ('whatfrom_requests_in_progress{path="/recommend"}', "whatfrom_recommend_active")

INSTANT = {
    "started_total": "sum(last_over_time(whatfrom_recommend_started_total[{w}]))",
    "outcomes_total": "sum by (outcome) (last_over_time(whatfrom_recommend_outcomes_total[{w}]))",
    "rejected_total": "sum(last_over_time(whatfrom_recommend_rejected_total[{w}]))",
    "http_requests_total": (
        'sum by (status) (last_over_time(whatfrom_http_request_seconds_count{{path="/recommend"}}'
        "[{w}]))"
    ),
    "stage_errors_total": "sum by (stage) (last_over_time(whatfrom_stage_errors_total[{w}]))",
    "active_max": "max_over_time(whatfrom_recommend_active[{w}])",
    "limit": "max_over_time(whatfrom_recommend_limit[{w}])",
    "in_progress_max": 'max_over_time(whatfrom_requests_in_progress{{path="/recommend"}}[{w}])',
    "threadpool_wait_p50": (
        "histogram_quantile(0.5, sum by (le) (increase("
        "whatfrom_threadpool_wait_seconds_bucket[{w}])))"
    ),
    "threadpool_wait_p95": (
        "histogram_quantile(0.95, sum by (le) (increase("
        "whatfrom_threadpool_wait_seconds_bucket[{w}])))"
    ),
    "stage_seconds_p50": (
        "histogram_quantile(0.5, sum by (le, stage) (increase(whatfrom_stage_seconds_bucket[{w}])))"
    ),
}
PER_MINUTE = {
    "started_per_min": "sum(increase(whatfrom_recommend_started_total[1m]))",
    "http_200_per_min": (
        'sum(increase(whatfrom_http_request_seconds_count{path="/recommend",status="200"}[1m]))'
    ),
    "rejected_per_min": "sum(increase(whatfrom_recommend_rejected_total[1m]))",
    "active_max_per_min": "max_over_time(whatfrom_recommend_active[1m])",
    "in_progress_max_per_min": (
        'max_over_time(whatfrom_requests_in_progress{path="/recommend"}[1m])'
    ),
}


def _get(path: str, **params: str) -> list[dict]:
    url = PROMETHEUS + path + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=30) as response:
        return json.load(response)["data"]["result"]


def _value(expr: str) -> float | None:
    rows = _get("query", query=expr)
    return float(rows[0]["value"][1]) if rows else None


def wait_idle(timeout: float) -> None:
    # 값이 아예 없으면(None) 앱이 안 떠 있거나 Prometheus가 아직 수집하지 못한 것이다. 이 경우는
    # 전체 timeout을 기다려도 나타나지 않으므로, 몇 번만 짧게 확인하고 바로 알려 준다.
    for _ in range(3):
        values = [_value(expr) for expr in IDLE]
        if all(v is not None for v in values):
            break
        time.sleep(SCRAPE_SECONDS)
    else:
        missing = [expr for expr, v in zip(IDLE, values, strict=True) if v is None]
        raise SystemExit(
            f"{missing[0]} 지표가 Prometheus에 없다. 측정한 앱이 떠 있고 Prometheus가 수집 중인지 "
            "확인한다"
        )

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if all(_value(expr) == 0 for expr in IDLE):
            # 0이 된 값이 Prometheus에 한 번 더 수집될 시간을 둔다.
            time.sleep(2 * SCRAPE_SECONDS)
            return
        time.sleep(SCRAPE_SECONDS)
    raise SystemExit(f"{timeout}초 안에 진행 중 요청과 점유한 자리가 0이 되지 않았다")


def main() -> None:
    parser = argparse.ArgumentParser(description="부하 측정 뒤 Prometheus 지표를 저장한다")
    parser.add_argument("--start", required=True, help="측정 시작 시각(UTC ISO8601)")
    parser.add_argument("--out", required=True)
    parser.add_argument("--timeout", type=float, default=900)
    args = parser.parse_args()

    wait_idle(args.timeout)
    start = datetime.fromisoformat(args.start.replace("Z", "+00:00"))
    end = datetime.now(UTC)
    window = f"{int((end - start).total_seconds()) + 60}s"
    stamp = end.strftime("%Y-%m-%dT%H:%M:%SZ")

    document: dict = {"start": args.start, "end": stamp, "window": window, "queries": {}}
    for name, template in INSTANT.items():
        promql = template.format(w=window)
        rows = _get("query", query=promql, time=stamp)
        document["queries"][name] = {
            "promql": promql,
            "result": [{"labels": r["metric"], "value": r["value"][1]} for r in rows],
        }
    for name, promql in PER_MINUTE.items():
        rows = _get("query_range", query=promql, start=args.start, end=stamp, step="60")
        document["queries"][name] = {
            "promql": promql,
            "start": args.start,
            "end": stamp,
            "step_seconds": 60,
            "values": [v[1] for v in rows[0]["values"]] if rows else [],
        }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        json.dump(document, f, ensure_ascii=False, indent=1)
    print(args.out, "저장")


if __name__ == "__main__":
    main()
