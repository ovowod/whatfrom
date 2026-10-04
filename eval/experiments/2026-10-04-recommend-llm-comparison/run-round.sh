#!/usr/bin/env bash
# M1-b 측정 회차 하나를 돌린다. 프로젝트 루트에서 실행한다.
#
#   eval/experiments/2026-10-04-recommend-llm-comparison/run-round.sh [회차] [Gemini 간격] [설정...]
#
# 회차 기본값은 1, Gemini 간격 기본값은 6초다. 설정을 주지 않으면 11개를 모두 잰다.
# 하루(KST) 안에 이어서 돌리고, 측정 중에는 collect·index를 돌리지 않는다.
#
# 다시 실행하면 이미 있는 snapshot과 결과는 건너뛰고 남은 단계부터 잇는다.
# 검증에 실패한 설정이 있으면 그 자리에서 멈춘다.
set -euo pipefail

EXP=eval/experiments/2026-10-04-recommend-llm-comparison
RUN=(uv run python "$EXP/recommend_experiment.py")
ROUND=${1:-1}
# AI Studio의 RPM 한도로 계산한 값(60 / RPM × 2). 다시 잴 때마다 2배로 늘린다.
GEMINI_INTERVAL=${2:-6}
shift $(($# < 2 ? $# : 2))
# kimi-max부터 재고, 같은 quota를 쓰는 Gemini 두 설정은 이어서 재지 않는다.
CONFIGS=("$@")
if [ ${#CONFIGS[@]} -eq 0 ]; then
  CONFIGS=(kimi-max kimi-low gemini-minimal luna-none luna-low sol-low
           gemini-low grok-none grok-low sonnet-min sonnet-low)
fi

snapshot() {
  if [ -e "$EXP/snapshots/r$ROUND-$1.json" ]; then
    echo "건너뜀: $ROUND회차 $1 snapshot이 이미 있다"
  else
    "${RUN[@]}" snapshot "$ROUND" "$1"
  fi
}

snapshot before

total=${#CONFIGS[@]}
index=0
for config in "${CONFIGS[@]}"; do
  index=$((index + 1))
  if [ -e "$EXP/results/$config-r$ROUND.json" ]; then
    echo "건너뜀: $config $ROUND회차 결과가 이미 있다"
    continue
  fi
  echo
  echo "=== [$index/$total] $config $ROUND회차 측정 시작 ($(date '+%H:%M:%S')) ==="
  if [[ $config == gemini-* ]]; then
    "${RUN[@]}" measure "$config" "$ROUND" --interval "$GEMINI_INTERVAL"
  else
    "${RUN[@]}" measure "$config" "$ROUND"
  fi
done

snapshot after
"${RUN[@]}" summary
