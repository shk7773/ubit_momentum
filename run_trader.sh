#!/bin/bash
# 모멘텀 트레이딩 봇 실행 스크립트

cd "$(dirname "$0")"

# 가상환경 활성화
source venv/bin/activate

# 로그 디렉토리 생성
mkdir -p logs

# 로그 파일명 설정 (trading_YYYYMMDD_HHMMSS.log)
LOG_FILE="logs/trading_$(date +%Y%m%d_%H%M%S).log"

echo "📝 로그 파일: $LOG_FILE"
echo "🚀 모멘텀 트레이딩 봇 시작..."
echo "   Ctrl+C로 종료할 수 있습니다."
echo ""

# unbuffered 모드(-u)로 실행하여 로그 실시간 기록 + tee로 화면/파일 동시 출력
python -u momentum_trader.py 2>&1 | tee "$LOG_FILE"
