#!/bin/bash
# 모멘텀 트레이딩 봇 실행 스크립트

cd "$(dirname "$0")"

# 가상환경 활성화
source venv/bin/activate

# 봇 실행
echo "🚀 모멘텀 트레이딩 봇 시작..."
echo "   Ctrl+C로 종료할 수 있습니다."
echo ""

python momentum_trader.py
