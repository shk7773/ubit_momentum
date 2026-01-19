#!/usr/bin/env python3
"""
=================================================================================
🚀 달리는 말에 올라타는 모멘텀 트레이딩 봇 (Momentum Riding Strategy)
=================================================================================

전략 개요:
- 거시적 관점: 일/주/월 캔들로 전반적인 시장 추세 분석
- 미시적 관점: 분/초 캔들로 실시간 모멘텀 감지 및 진입
- 빠른 상승 시 시장가 매수, 지속적인 익절가 조정
- 하락 전환 시 빠른 익절/손절

핵심 원칙:
1. 전체 하락장에서는 관망
2. 상승 모멘텀 감지 시 빠르게 진입
3. 수익 보호를 위한 트레일링 스탑
4. 손실 최소화를 위한 빠른 손절
"""

import os
import sys
import time
import json
import uuid
import asyncio
import hashlib
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Any
from collections import deque
from urllib.parse import urlencode

import jwt
import requests
import websockets
from dotenv import load_dotenv

# =================================================================================
# 📊 전략 파라미터 (Strategy Parameters) - 여기서 조절 가능
# =================================================================================

# === 투자 설정 ===
MAX_INVESTMENT = 1_000_000          # 최대 투자금 (원)
MIN_ORDER_AMOUNT = 5_000            # 최소 주문 금액 (업비트 최소금액 5,000원 + 버퍼)
TRADING_FEE_RATE = 0.0005           # 거래 수수료 (0.05% = 0.0005)

# === 거시적 분석 (Macro Analysis) - 전체 시장 추세 ===
MACRO_LOOKBACK_DAYS = 7             # 일봉 분석 기간 (일)
MACRO_MIN_CHANGE_RATE = -0.02       # 전체 하락장 판단 기준 (-2% 이하면 관망)
MACRO_BULLISH_THRESHOLD = 0.01     # 상승장 판단 기준 (+1% 이상)
MACRO_UPDATE_INTERVAL = 300         # 거시 분석 갱신 주기 (초)

# === 미시적 분석 (Micro Analysis) - 진입 신호 ===
MOMENTUM_WINDOW = 10                # 모멘텀 계산 윈도우 (캔들 개수)
MOMENTUM_THRESHOLD = 0.003          # 진입 모멘텀 기준 (0.3% 상승률)
VOLUME_SPIKE_RATIO = 1.5            # 거래량 급등 배율 (평균 대비)
CONSECUTIVE_UP_CANDLES = 3          # 연속 상승 캔들 개수

# === 초봉 분석 (Second Candle Analysis) - 실시간 변화 감지 ===
SECOND_CANDLE_UNIT = 5              # 초봉 단위 (1, 3, 5, 10, 30, 60 중 선택)
SECOND_MOMENTUM_WINDOW = 12         # 초봉 모멘텀 윈도우 (개수)
SECOND_MOMENTUM_THRESHOLD = 0.001   # 초봉 모멘텀 기준 (0.1% - 더 민감)
SECOND_RAPID_RISE_THRESHOLD = 0.002 # 급등 판단 기준 (0.2%/5초)

# === 익절/손절 설정 ===
INITIAL_STOP_LOSS = 0.005           # 초기 손절선 (0.5%)
TRAILING_STOP_ACTIVATION = 0.003    # 트레일링 스탑 활성화 기준 (+0.3% 수익 시)
TRAILING_STOP_DISTANCE = 0.004      # 트레일링 스탑 거리 (0.4% - 고점 대비)
TAKE_PROFIT_TARGET = 0.01           # 목표 수익률 (1% - 트레일링으로 더 추적)
MAX_HOLDING_TIME = 1800             # 최대 보유 시간 (초, 30분)

# === 리스크 관리 ===
MAX_TRADES_PER_HOUR = 10            # 시간당 최대 거래 횟수
COOL_DOWN_AFTER_LOSS = 180          # 손절 후 대기 시간 (초)
MIN_PRICE_STABILITY = 0.005         # 최소 가격 안정성 (급등락 필터)

# === 시스템 설정 ===
MARKET = "KRW-BTC"                  # 거래 마켓
CANDLE_UNIT = 1                     # 분봉 단위 (1분)
LOG_LEVEL = logging.INFO            # 로그 레벨
DRY_RUN = True                      # 테스트 모드 (True: 실제 거래 X)
USE_SECOND_CANDLES = True           # 초봉 사용 여부

# =================================================================================
# 🔧 시스템 설정
# =================================================================================

# 환경변수 로드
load_dotenv()

# API 키 설정
ACCESS_KEY = os.getenv("UPBIT_ACCESS_KEY")
SECRET_KEY = os.getenv("UPBIT_SECRET_KEY")

# API 엔드포인트
REST_BASE_URL = "https://api.upbit.com/v1"
WS_PUBLIC_URL = "wss://api.upbit.com/websocket/v1"
WS_PRIVATE_URL = "wss://api.upbit.com/websocket/v1/private"

# 로깅 설정
logging.basicConfig(
    level=LOG_LEVEL,
    format='%(asctime)s | %(levelname)-7s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)


class UpbitAPI:
    """업비트 REST API 클라이언트"""
    
    def __init__(self, access_key: str, secret_key: str):
        self.access_key = access_key
        self.secret_key = secret_key
        self.session = requests.Session()
        
    def _generate_jwt(self, query: Optional[Dict] = None) -> str:
        """JWT 토큰 생성"""
        payload = {
            'access_key': self.access_key,
            'nonce': str(uuid.uuid4()),
        }
        
        if query:
            query_string = urlencode(query).encode()
            m = hashlib.sha512()
            m.update(query_string)
            payload['query_hash'] = m.hexdigest()
            payload['query_hash_alg'] = 'SHA512'
            
        return jwt.encode(payload, self.secret_key)
    
    def _request(self, method: str, endpoint: str, 
                 params: Optional[Dict] = None, 
                 data: Optional[Dict] = None) -> Dict:
        """API 요청 수행"""
        url = f"{REST_BASE_URL}{endpoint}"
        headers = {}
        
        if params or data:
            token = self._generate_jwt(params or data)
            headers['Authorization'] = f"Bearer {token}"
        elif endpoint.startswith('/orders') or endpoint == '/accounts':
            token = self._generate_jwt()
            headers['Authorization'] = f"Bearer {token}"
            
        try:
            if method == 'GET':
                response = self.session.get(url, params=params, headers=headers)
            elif method == 'POST':
                headers['Content-Type'] = 'application/json; charset=utf-8'
                response = self.session.post(url, json=data, headers=headers)
            elif method == 'DELETE':
                response = self.session.delete(url, params=params, headers=headers)
            else:
                raise ValueError(f"Unsupported method: {method}")
                
            response.raise_for_status()
            return response.json()
            
        except requests.exceptions.RequestException as e:
            logger.error(f"API 요청 실패: {e}")
            if hasattr(e, 'response') and e.response:
                logger.error(f"응답: {e.response.text}")
            raise
            
    def get_accounts(self) -> List[Dict]:
        """계정 잔고 조회"""
        return self._request('GET', '/accounts')
    
    def get_ticker(self, markets: str) -> List[Dict]:
        """현재가 조회"""
        return self._request('GET', '/ticker', params={'markets': markets})
    
    def get_candles_minutes(self, market: str, unit: int = 1, 
                           count: int = 200, to: Optional[str] = None) -> List[Dict]:
        """분봉 조회"""
        params = {'market': market, 'count': count}
        if to:
            params['to'] = to
        return self._request('GET', f'/candles/minutes/{unit}', params=params)
    
    def get_candles_days(self, market: str, count: int = 200) -> List[Dict]:
        """일봉 조회"""
        return self._request('GET', '/candles/days', 
                           params={'market': market, 'count': count})
    
    def get_candles_weeks(self, market: str, count: int = 10) -> List[Dict]:
        """주봉 조회"""
        return self._request('GET', '/candles/weeks',
                           params={'market': market, 'count': count})
    
    def get_candles_months(self, market: str, count: int = 6) -> List[Dict]:
        """월봉 조회"""
        return self._request('GET', '/candles/months',
                           params={'market': market, 'count': count})
    
    def get_candles_seconds(self, market: str, count: int = 60, 
                           to: Optional[str] = None) -> List[Dict]:
        """초봉 조회 (1초봉 고정, unit 파라미터 없음)"""
        params = {'market': market, 'count': count}
        if to:
            params['to'] = to
        return self._request('GET', '/candles/seconds', params=params)
    
    def get_orderbook(self, markets: str) -> List[Dict]:
        """호가 조회"""
        return self._request('GET', '/orderbook', params={'markets': markets})
    
    def place_order(self, market: str, side: str, ord_type: str,
                   volume: Optional[str] = None, price: Optional[str] = None,
                   identifier: Optional[str] = None) -> Dict:
        """주문 생성"""
        data = {
            'market': market,
            'side': side,
            'ord_type': ord_type
        }
        if volume:
            data['volume'] = volume
        if price:
            data['price'] = price
        if identifier:
            data['identifier'] = identifier
            
        return self._request('POST', '/orders', data=data)
    
    def cancel_order(self, uuid_val: str) -> Dict:
        """주문 취소"""
        return self._request('DELETE', '/order', params={'uuid': uuid_val})
    
    def get_order(self, uuid_val: str) -> Dict:
        """주문 조회"""
        return self._request('GET', '/order', params={'uuid': uuid_val})
    
    def get_orders_chance(self, market: str) -> Dict:
        """주문 가능 정보 조회"""
        return self._request('GET', '/orders/chance', params={'market': market})


class TradingState:
    """거래 상태 관리"""
    
    def __init__(self):
        self.position = None              # 현재 포지션 정보
        self.entry_price = 0.0            # 진입 가격
        self.entry_time = None            # 진입 시간
        self.highest_price = 0.0          # 보유 중 최고가
        self.stop_loss_price = 0.0        # 손절가
        self.take_profit_price = 0.0      # 익절가
        self.trailing_active = False      # 트레일링 스탑 활성화 여부
        
        # 거래 기록
        self.trades_today = []            # 오늘 거래 기록
        self.last_trade_time = None       # 마지막 거래 시간
        self.last_loss_time = None        # 마지막 손절 시간
        
        # 수익 추적
        self.total_profit = 0.0           # 총 수익
        self.total_trades = 0             # 총 거래 횟수
        self.winning_trades = 0           # 수익 거래
        self.losing_trades = 0            # 손실 거래
        
    def has_position(self) -> bool:
        """포지션 보유 여부"""
        return self.position is not None
    
    def can_trade(self) -> bool:
        """거래 가능 여부 확인"""
        now = datetime.now()
        
        # 시간당 거래 횟수 제한
        hour_ago = now - timedelta(hours=1)
        recent_trades = [t for t in self.trades_today 
                        if t['time'] > hour_ago]
        if len(recent_trades) >= MAX_TRADES_PER_HOUR:
            return False
            
        # 손절 후 쿨다운
        if self.last_loss_time:
            cooldown_end = self.last_loss_time + timedelta(seconds=COOL_DOWN_AFTER_LOSS)
            if now < cooldown_end:
                return False
                
        return True
    
    def record_trade(self, trade_type: str, amount: float, 
                    price: float, profit: float = 0.0):
        """거래 기록"""
        trade = {
            'time': datetime.now(),
            'type': trade_type,
            'amount': amount,
            'price': price,
            'profit': profit
        }
        self.trades_today.append(trade)
        self.last_trade_time = trade['time']
        self.total_trades += 1
        
        if trade_type in ['take_profit', 'trailing_stop', 'time_exit']:
            if profit > 0:
                self.winning_trades += 1
            else:
                self.losing_trades += 1
            self.total_profit += profit
            
        if trade_type == 'stop_loss':
            self.last_loss_time = trade['time']
            self.losing_trades += 1
            self.total_profit += profit


class MarketAnalyzer:
    """시장 분석기"""
    
    def __init__(self, api: UpbitAPI):
        self.api = api
        self.macro_trend = None           # 거시 추세 (bullish/bearish/neutral)
        self.macro_score = 0.0            # 거시 점수
        self.last_macro_update = None     # 마지막 거시 분석 시간
        
        # 캔들 데이터 캐시
        self.minute_candles = deque(maxlen=200)
        self.second_candles = deque(maxlen=120)  # 초봉 캐시 (최근 2분)
        self.volume_history = deque(maxlen=100)
        self.second_volume_history = deque(maxlen=60)
        
    def analyze_macro(self) -> Dict:
        """시장 추세 분석 (중단기 + 거시 혼합)"""
        try:
            # 1. 일봉 분석 (최근 3일 중심)
            daily = self.api.get_candles_days(MARKET, count=4)
            daily_change = 0.0
            if len(daily) >= 2:
                daily_change = (daily[0]['trade_price'] - daily[1]['trade_price']) / daily[1]['trade_price']
            
            # 2. 4시간봉 분석 (최근 24시간 추세)
            h4 = self.api.get_candles_minutes(MARKET, unit=240, count=6)
            h4_change = 0.0
            if len(h4) >= 2:
                h4_change = (h4[0]['trade_price'] - h4[-1]['opening_price']) / h4[-1]['opening_price']
            
            # 3. 1시간봉 분석 (최근 6시간 추세 - 가장 민감)
            h1 = self.api.get_candles_minutes(MARKET, unit=60, count=6)
            h1_change = 0.0
            if len(h1) >= 2:
                h1_change = (h1[0]['trade_price'] - h1[-1]['opening_price']) / h1[-1]['opening_price']

            # 4. 주봉/월봉 (장기 배경)
            weekly = self.api.get_candles_weeks(MARKET, count=2)
            weekly_change = (weekly[0]['trade_price'] - weekly[1]['trade_price']) / weekly[1]['trade_price'] if len(weekly) >= 2 else 0

            # 종합 점수 계산 (중단기 가중치 강화)
            # 1시간(40%) + 4시간(30%) + 1일(20%) + 주봉(10%)
            score = h1_change * 0.4 + h4_change * 0.3 + daily_change * 0.2 + weekly_change * 0.1
            
            # 추세 판단 기준 (더 유연하게 변경)
            # h1_change가 매우 높으면(급반등) 다른 조건 무시하고 거래 허용 가능
            rapid_recovery = h1_change > 0.01  # 최근 6시간 1% 이상 상승 시
            
            if score < MACRO_MIN_CHANGE_RATE and not rapid_recovery:
                trend = 'bearish'
                can_trade = False
            elif score > MACRO_BULLISH_THRESHOLD or rapid_recovery:
                trend = 'bullish'
                can_trade = True
            else:
                trend = 'neutral'
                can_trade = True
            
            self.macro_trend = trend
            self.macro_score = score
            self.last_macro_update = datetime.now()
            
            result = {
                'trend': trend,
                'score': score,
                'can_trade': can_trade,
                'h1_change': h1_change,
                'h4_change': h4_change,
                'daily_change': daily_change,
                'rapid_recovery': rapid_recovery
            }
            
            log_msg = f"📈 추세 분석 | {trend} (점수:{score:.4f}) | 1h:{h1_change*100:+.2f}% 4h:{h4_change*100:+.2f}% 일:{daily_change*100:+.2f}%"
            if rapid_recovery:
                log_msg += " | 🚀 단기 급반등 감지됨"
            logger.info(log_msg)
            
            return result
            
        except Exception as e:
            logger.error(f"거시 분석 오류: {e}")
            return {'trend': 'neutral', 'score': 0, 'can_trade': True}
    
    def update_candles(self, candles: List[Dict]):
        """분봉 데이터 업데이트"""
        for candle in reversed(candles):  # 시간순 정렬
            self.minute_candles.append(candle)
            self.volume_history.append(candle['candle_acc_trade_volume'])
    
    def update_second_candles(self, candles: List[Dict]):
        """초봉 데이터 업데이트"""
        for candle in reversed(candles):  # 시간순 정렬
            self.second_candles.append(candle)
            self.second_volume_history.append(candle['candle_acc_trade_volume'])
    
    def detect_momentum(self, current_price: float) -> Dict:
        """모멘텀 감지 (분봉 기반)"""
        if len(self.minute_candles) < MOMENTUM_WINDOW:
            return {'signal': False, 'strength': 0, 'reason': '분봉 데이터 부족'}
        
        recent = list(self.minute_candles)[-MOMENTUM_WINDOW:]
        
        # 1. 가격 모멘텀 계산 (분봉)
        price_change = (current_price - recent[0]['opening_price']) / recent[0]['opening_price']
        
        # 2. 연속 상승 캔들 확인
        up_count = 0
        for i in range(1, len(recent)):
            if recent[i]['trade_price'] > recent[i-1]['trade_price']:
                up_count += 1
            else:
                up_count = 0  # 연속성 깨지면 리셋
        
        # 3. 거래량 급등 확인
        avg_volume = sum(self.volume_history) / len(self.volume_history) if self.volume_history else 0
        recent_volume = recent[-1]['candle_acc_trade_volume']
        volume_ratio = recent_volume / avg_volume if avg_volume > 0 else 0
        
        # 4. 시그널 판단
        momentum_ok = price_change >= MOMENTUM_THRESHOLD
        volume_ok = volume_ratio >= VOLUME_SPIKE_RATIO
        consecutive_ok = up_count >= CONSECUTIVE_UP_CANDLES
        
        # 가격 안정성 체크 (급등락 필터)
        high = max(c['high_price'] for c in recent)
        low = min(c['low_price'] for c in recent)
        volatility = (high - low) / low
        stable = volatility < MIN_PRICE_STABILITY * 5  # 너무 변동성 크면 패스
        
        signal = momentum_ok and (volume_ok or consecutive_ok) and stable
        
        # 신호 강도 계산
        strength = 0
        if momentum_ok:
            strength += price_change / MOMENTUM_THRESHOLD * 30
        if volume_ok:
            strength += (volume_ratio - 1) * 20
        if consecutive_ok:
            strength += up_count * 10
        strength = min(strength, 100)
        
        reason = []
        if momentum_ok:
            reason.append(f"분봉모멘텀 {price_change*100:.2f}%")
        if volume_ok:
            reason.append(f"거래량 {volume_ratio:.1f}배")
        if consecutive_ok:
            reason.append(f"연속상승 {up_count}개")
        if not stable:
            reason.append("변동성 과다")
            
        return {
            'signal': signal,
            'strength': strength,
            'price_change': price_change,
            'volume_ratio': volume_ratio,
            'up_count': up_count,
            'reason': ' / '.join(reason) if reason else '조건 미충족'
        }
    
    def detect_second_momentum(self, current_price: float) -> Dict:
        """초봉 기반 실시간 모멘텀 감지 (더 빠른 반응)"""
        if len(self.second_candles) < SECOND_MOMENTUM_WINDOW:
            return {'signal': False, 'strength': 0, 'reason': '초봉 데이터 부족', 'rapid_rise': False}
        
        recent = list(self.second_candles)[-SECOND_MOMENTUM_WINDOW:]
        
        # 1. 초단위 가격 모멘텀
        sec_price_change = (current_price - recent[0]['opening_price']) / recent[0]['opening_price']
        
        # 2. 급등 감지 (최근 5초 내 급격한 상승)
        if len(recent) >= 2:
            rapid_change = (current_price - recent[-2]['opening_price']) / recent[-2]['opening_price']
        else:
            rapid_change = 0
        rapid_rise = rapid_change >= SECOND_RAPID_RISE_THRESHOLD
        
        # 3. 연속 상승 초봉 확인
        sec_up_count = 0
        for i in range(1, len(recent)):
            if recent[i]['trade_price'] > recent[i-1]['trade_price']:
                sec_up_count += 1
            else:
                sec_up_count = 0
        
        # 4. 초봉 거래량 급등
        avg_sec_volume = sum(self.second_volume_history) / len(self.second_volume_history) if self.second_volume_history else 0
        recent_sec_volume = recent[-1]['candle_acc_trade_volume']
        sec_volume_ratio = recent_sec_volume / avg_sec_volume if avg_sec_volume > 0 else 0
        
        # 시그널 판단
        sec_momentum_ok = sec_price_change >= SECOND_MOMENTUM_THRESHOLD
        sec_volume_ok = sec_volume_ratio >= VOLUME_SPIKE_RATIO
        
        signal = (sec_momentum_ok and sec_volume_ok) or rapid_rise
        
        # 강도 계산
        strength = 0
        if sec_momentum_ok:
            strength += sec_price_change / SECOND_MOMENTUM_THRESHOLD * 25
        if sec_volume_ok:
            strength += (sec_volume_ratio - 1) * 15
        if rapid_rise:
            strength += rapid_change / SECOND_RAPID_RISE_THRESHOLD * 30
        strength = min(strength, 100)
        
        reason = []
        if sec_momentum_ok:
            reason.append(f"초봉모멘텀 {sec_price_change*100:.3f}%")
        if rapid_rise:
            reason.append(f"🚀급등 {rapid_change*100:.3f}%")
        if sec_volume_ok:
            reason.append(f"초봉거래량 {sec_volume_ratio:.1f}배")
        if sec_up_count >= 3:
            reason.append(f"연속상승초 {sec_up_count}개")
        
        return {
            'signal': signal,
            'strength': strength,
            'price_change': sec_price_change,
            'rapid_change': rapid_change,
            'rapid_rise': rapid_rise,
            'volume_ratio': sec_volume_ratio,
            'up_count': sec_up_count,
            'reason': ' / '.join(reason) if reason else '조건 미충족'
        }
    
    def detect_combined_momentum(self, current_price: float) -> Dict:
        """분봉 + 초봉 결합 모멘텀 감지"""
        minute_result = self.detect_momentum(current_price)
        
        # 초봉 사용 안함이면 분봉만 반환
        if not USE_SECOND_CANDLES or len(self.second_candles) < SECOND_MOMENTUM_WINDOW:
            return minute_result
        
        second_result = self.detect_second_momentum(current_price)
        
        # 분봉 기본조건 + 초봉 확인으로 정밀도 향상
        # 케이스 1: 분봉 신호 O + 초봉 확인 = 강력한 신호
        # 케이스 2: 분봉 신호 X + 초봉 급등 = 빠른 진입 기회
        
        combined_signal = False
        combined_strength = 0
        reasons = []
        
        if minute_result['signal'] and second_result['signal']:
            # 둘 다 신호: 매우 강력
            combined_signal = True
            combined_strength = min(100, minute_result['strength'] * 0.6 + second_result['strength'] * 0.4)
            reasons.append(minute_result['reason'])
            reasons.append(second_result['reason'])
            
        elif second_result['rapid_rise']:
            # 초봉 급등만 감지: 빠른 진입 (분봉 조건 완화)
            if minute_result['price_change'] > MOMENTUM_THRESHOLD * 0.5:  # 분봉 조건 50%만 충족해도 OK
                combined_signal = True
                combined_strength = second_result['strength']
                reasons.append(f"⚡빠른진입: {second_result['reason']}")
                
        elif minute_result['signal']:
            # 분봉 신호만: 일반 진입
            combined_signal = True
            combined_strength = minute_result['strength'] * 0.8
            reasons.append(minute_result['reason'])
        
        return {
            'signal': combined_signal,
            'strength': combined_strength,
            'minute_signal': minute_result['signal'],
            'second_signal': second_result['signal'],
            'rapid_rise': second_result.get('rapid_rise', False),
            'reason': ' | '.join(reasons) if reasons else '조건 미충족'
        }


class MomentumTrader:
    """모멘텀 트레이딩 봇"""
    
    def __init__(self):
        if not ACCESS_KEY or not SECRET_KEY:
            raise ValueError("API 키가 설정되지 않았습니다. .env 파일을 확인하세요.")
            
        self.api = UpbitAPI(ACCESS_KEY, SECRET_KEY)
        self.analyzer = MarketAnalyzer(self.api)
        self.state = TradingState()
        self.running = False
        
        # 현재 가격 추적
        self.current_price = 0.0
        self.last_price_update = None
        
    async def start(self):
        """트레이딩 봇 시작"""
        logger.info("=" * 60)
        logger.info("🚀 모멘텀 트레이딩 봇 시작")
        logger.info(f"   마켓: {MARKET}")
        logger.info(f"   최대 투자금: {MAX_INVESTMENT:,}원")
        logger.info(f"   테스트 모드: {'ON' if DRY_RUN else 'OFF'}")
        logger.info("=" * 60)
        
        # 초기 잔고 확인
        self._check_balance()
        
        # 초기 거시 분석
        macro = self.analyzer.analyze_macro()
        
        self.running = True
        
        try:
            await asyncio.gather(
                self._price_monitor(),
                self._trading_loop(),
                self._macro_update_loop()
            )
        except KeyboardInterrupt:
            logger.info("사용자에 의해 중단됨")
        except Exception as e:
            logger.error(f"봇 오류: {e}")
        finally:
            self.running = False
            self._print_summary()
    
    def _check_balance(self):
        """잔고 확인"""
        try:
            accounts = self.api.get_accounts()
            for acc in accounts:
                if acc['currency'] == 'KRW':
                    balance = float(acc['balance'])
                    logger.info(f"💰 KRW 잔고: {balance:,.0f}원")
                elif acc['currency'] == 'BTC':
                    balance = float(acc['balance'])
                    locked = float(acc['locked'])
                    logger.info(f"₿ BTC 잔고: {balance:.8f} (잠김: {locked:.8f})")
        except Exception as e:
            logger.error(f"잔고 확인 실패: {e}")
    
    async def _price_monitor(self):
        """WebSocket으로 실시간 가격 모니터링"""
        while self.running:
            try:
                async with websockets.connect(WS_PUBLIC_URL) as ws:
                    # 구독 요청
                    subscribe = [
                        {"ticket": f"momentum-{uuid.uuid4()}"},
                        {"type": "ticker", "codes": [MARKET]},
                        {"type": "trade", "codes": [MARKET]},
                        {"format": "SIMPLE"}
                    ]
                    await ws.send(json.dumps(subscribe))
                    logger.info("📡 WebSocket 연결됨 - 실시간 가격 모니터링 시작")
                    
                    # PING 타이머
                    last_ping = time.time()
                    
                    while self.running:
                        try:
                            # PING 전송 (60초마다)
                            if time.time() - last_ping > 60:
                                await ws.send("PING")
                                last_ping = time.time()
                            
                            # 메시지 수신
                            msg = await asyncio.wait_for(ws.recv(), timeout=30)
                            
                            if msg == "PONG":
                                continue
                                
                            data = json.loads(msg)
                            
                            if data.get('ty') == 'ticker':
                                self.current_price = data['tp']
                                self.last_price_update = datetime.now()
                                
                            elif data.get('type') == 'trade':
                                self.current_price = data.get('trade_price', self.current_price)
                                self.last_price_update = datetime.now()
                                
                        except asyncio.TimeoutError:
                            await ws.send("PING")
                            last_ping = time.time()
                            
            except websockets.exceptions.ConnectionClosed:
                logger.warning("WebSocket 연결 끊김, 재연결 시도...")
                await asyncio.sleep(3)
            except Exception as e:
                logger.error(f"WebSocket 오류: {e}")
                await asyncio.sleep(5)
    
    async def _trading_loop(self):
        """메인 트레이딩 루프"""
        # 가격 데이터 수신 대기
        await asyncio.sleep(5)
        last_status_log = 0
        
        while self.running:
            try:
                if self.current_price <= 0:
                    await asyncio.sleep(1)
                    continue
                
                # 거시 분석 결과 확인
                if self.analyzer.macro_trend == 'bearish':
                    if not self.state.has_position():
                        logger.debug("📉 하락장 관망 중...")
                        await asyncio.sleep(10)
                        continue
                
                if self.state.has_position():
                    # 포지션 관리
                    await self._manage_position()
                else:
                    # 진입 기회 탐색
                    await self._find_entry()
                    
                    # 30초마다 분석 상태 로그 (포지션 없을 때)
                    now = time.time()
                    if now - last_status_log >= 30:
                        last_status_log = now
                        # 상세 분석 정보 수집
                        min_result = self.analyzer.detect_momentum(self.current_price)
                        sec_result = self.analyzer.detect_second_momentum(self.current_price) if USE_SECOND_CANDLES else {}
                        
                        min_change = min_result.get('price_change', 0) * 100
                        vol_ratio = min_result.get('volume_ratio', 0)
                        sec_change = sec_result.get('price_change', 0) * 100 if sec_result else 0
                        
                        logger.info(f"📊 {self.current_price:,.0f}원 | "
                                  f"분봉:{min_change:+.2f}% 초봉:{sec_change:+.3f}% | "
                                  f"거래량:{vol_ratio:.1f}배 | 강도:{min_result['strength']:.0f}")
                    
                await asyncio.sleep(1)  # 1초마다 체크
                
            except Exception as e:
                logger.error(f"트레이딩 루프 오류: {e}")
                await asyncio.sleep(5)
    
    async def _macro_update_loop(self):
        """거시 분석 주기적 업데이트"""
        while self.running:
            await asyncio.sleep(MACRO_UPDATE_INTERVAL)
            try:
                self.analyzer.analyze_macro()
            except Exception as e:
                logger.error(f"거시 분석 업데이트 오류: {e}")
    
    async def _find_entry(self):
        """진입 기회 탐색 (분봉 + 초봉 결합 분석)"""
        if not self.state.can_trade():
            return
            
        try:
            # 최신 분봉 데이터 가져오기
            candles = self.api.get_candles_minutes(MARKET, CANDLE_UNIT, MOMENTUM_WINDOW * 2)
            self.analyzer.update_candles(candles)
            
            # 초봉 데이터 가져오기 (사용 설정 시)
            if USE_SECOND_CANDLES:
                try:
                    second_candles = self.api.get_candles_seconds(
                        MARKET, SECOND_MOMENTUM_WINDOW * 2
                    )
                    self.analyzer.update_second_candles(second_candles)
                except Exception as e:
                    logger.debug(f"초봉 조회 실패 (무시): {e}")
            
            # 결합 모멘텀 감지 (분봉 + 초봉)
            momentum = self.analyzer.detect_combined_momentum(self.current_price)
            
            if momentum['signal']:
                rapid_indicator = "🚀" if momentum.get('rapid_rise') else "🎯"
                logger.info(f"{rapid_indicator} 진입 신호 감지! | {momentum['reason']} | "
                          f"강도: {momentum['strength']:.1f}")
                await self._execute_buy()
                
        except Exception as e:
            logger.error(f"진입 탐색 오류: {e}")
    
    async def _execute_buy(self):
        """매수 실행"""
        try:
            # 사용 가능 금액 확인
            accounts = self.api.get_accounts()
            krw_balance = 0.0
            for acc in accounts:
                if acc['currency'] == 'KRW':
                    krw_balance = float(acc['balance'])
                    break
            
            # 투자금 계산 (최대 투자금과 잔고 중 작은 값)
            invest_amount = min(MAX_INVESTMENT, krw_balance * 0.99)  # 99%만 사용 (수수료 대비)
            
            if invest_amount < MIN_ORDER_AMOUNT:
                logger.warning(f"잔고 부족: {krw_balance:,.0f}원")
                return
            
            if DRY_RUN:
                logger.info(f"🛒 [테스트] 시장가 매수 | 금액: {invest_amount:,.0f}원 | "
                          f"현재가: {self.current_price:,.0f}원")
                # 테스트 모드에서는 가상 포지션 생성
                self.state.position = {
                    'side': 'bid',
                    'price': self.current_price,
                    'amount': invest_amount,
                    'volume': invest_amount / self.current_price
                }
            else:
                # 실제 시장가 매수
                result = self.api.place_order(
                    market=MARKET,
                    side='bid',
                    ord_type='price',  # 시장가 매수
                    price=str(int(invest_amount))
                )
                logger.info(f"🛒 시장가 매수 주문 | UUID: {result['uuid']} | "
                          f"금액: {invest_amount:,.0f}원")
                
                # 체결 확인 (시장가 주문은 즉시 체결됨)
                for _ in range(10):
                    await asyncio.sleep(0.5)
                    order = self.api.get_order(result['uuid'])
                    executed_vol = float(order.get('executed_volume', 0))
                    
                    # 시장가 주문: executed_volume > 0이면 체결된 것
                    if executed_vol > 0:
                        # 체결가 계산 (trades가 있으면 사용, 없으면 현재가)
                        executed_price = self.current_price
                        if order.get('trades') and len(order['trades']) > 0:
                            executed_price = float(order['trades'][0]['price'])
                        
                        self.state.position = {
                            'uuid': order['uuid'],
                            'side': 'bid',
                            'price': executed_price,
                            'amount': invest_amount,
                            'volume': executed_vol
                        }
                        
                        state = order['state']
                        if state == 'cancel':
                            logger.info(f"   부분 체결 후 잔여 취소 (정상)")
                        break
            
            if self.state.position:
                self.state.entry_price = self.state.position['price']
                self.state.entry_time = datetime.now()
                self.state.highest_price = self.state.entry_price
                self.state.stop_loss_price = self.state.entry_price * (1 - INITIAL_STOP_LOSS)
                self.state.take_profit_price = self.state.entry_price * (1 + TAKE_PROFIT_TARGET)
                self.state.trailing_active = False
                
                self.state.record_trade('buy', invest_amount, self.state.entry_price)
                
                logger.info(f"✅ 매수 체결 | 가격: {self.state.entry_price:,.0f}원 | "
                          f"손절가: {self.state.stop_loss_price:,.0f}원 | "
                          f"익절가: {self.state.take_profit_price:,.0f}원")
                
        except Exception as e:
            logger.error(f"매수 실행 오류: {e}")
    
    async def _manage_position(self):
        """포지션 관리 (익절/손절 판단)"""
        if not self.state.has_position():
            return
            
        current = self.current_price
        entry = self.state.entry_price
        profit_rate = (current - entry) / entry
        
        # 최고가 업데이트
        if current > self.state.highest_price:
            self.state.highest_price = current
            
            # 트레일링 스탑 활성화 확인
            if profit_rate >= TRAILING_STOP_ACTIVATION and not self.state.trailing_active:
                self.state.trailing_active = True
                logger.info(f"📊 트레일링 스탑 활성화 | 수익률: {profit_rate*100:.2f}%")
            
            # 트레일링 스탑 가격 업데이트
            if self.state.trailing_active:
                new_stop = current * (1 - TRAILING_STOP_DISTANCE)
                if new_stop > self.state.stop_loss_price:
                    old_stop = self.state.stop_loss_price
                    self.state.stop_loss_price = new_stop
                    logger.debug(f"🔄 트레일링 스탑 갱신: {old_stop:,.0f} → {new_stop:,.0f}원")
        
        # 매도 조건 체크
        sell_reason = None
        
        # 1. 손절선 도달 (트레일링 스탑 발동 포함)
        if current <= self.state.stop_loss_price:
            if self.state.trailing_active:
                sell_reason = 'trailing_stop'
            else:
                sell_reason = 'stop_loss'
        
        # 2. 목표 수익률 도달 시 → 바로 익절하지 않고 트레일링 스탑 강화
        elif profit_rate >= TAKE_PROFIT_TARGET:
            if not self.state.trailing_active:
                # 트레일링 스탑 활성화
                self.state.trailing_active = True
                # 손절선을 매입가로 올림 (손실 없이 청산 보장)
                self.state.stop_loss_price = entry
                logger.info(f"🎯 목표 수익률 {TAKE_PROFIT_TARGET*100:.1f}% 도달! "
                          f"트레일링 활성화 (손절가→매입가: {entry:,.0f}원)")
            # 계속 상승 추세 추적 (바로 익절하지 않음)
        
        # 3. 최대 보유 시간 초과
        elif self.state.entry_time:
            holding_time = (datetime.now() - self.state.entry_time).total_seconds()
            if holding_time >= MAX_HOLDING_TIME:
                sell_reason = 'time_exit'
        
        if sell_reason:
            await self._execute_sell(sell_reason)
        else:
            # 상태 로깅 (10초마다)
            if int(time.time()) % 10 == 0:
                pnl = profit_rate * 100
                logger.info(f"📈 보유 중 | 현재가: {current:,.0f}원 | "
                          f"수익률: {pnl:+.2f}% | "
                          f"손절가: {self.state.stop_loss_price:,.0f}원")
    
    async def _execute_sell(self, reason: str):
        """매도 실행"""
        if not self.state.has_position():
            return
            
        try:
            volume = self.state.position.get('volume', 0)
            
            if DRY_RUN:
                executed_price = self.current_price
                logger.info(f"💵 [테스트] 시장가 매도 | 사유: {reason} | "
                          f"가격: {executed_price:,.0f}원")
            else:
                # 실제 시장가 매도
                result = self.api.place_order(
                    market=MARKET,
                    side='ask',
                    ord_type='market',  # 시장가 매도
                    volume=str(volume)
                )
                logger.info(f"💵 시장가 매도 주문 | UUID: {result['uuid']} | 사유: {reason}")
                
                # 체결 확인 (시장가 주문은 즉시 체결됨)
                executed_price = self.current_price
                for _ in range(10):
                    await asyncio.sleep(0.5)
                    order = self.api.get_order(result['uuid'])
                    executed_vol = float(order.get('executed_volume', 0))
                    
                    # 시장가 주문: executed_volume > 0이면 체결된 것
                    if executed_vol > 0:
                        if order.get('trades') and len(order['trades']) > 0:
                            executed_price = float(order['trades'][0]['price'])
                        break
            
            # 수익 계산
            buy_amount = self.state.position.get('amount', 0)
            sell_amount = volume * executed_price
            fee = (buy_amount + sell_amount) * TRADING_FEE_RATE
            profit = sell_amount - buy_amount - fee
            profit_rate = profit / buy_amount * 100 if buy_amount > 0 else 0
            
            # 상태 기록
            self.state.record_trade(reason, sell_amount, executed_price, profit)
            
            # 포지션 정리
            self.state.position = None
            self.state.trailing_active = False
            
            emoji = "🎉" if profit >= 0 else "📉"
            logger.info(f"{emoji} 매도 완료 | 사유: {reason} | "
                       f"수익: {profit:+,.0f}원 ({profit_rate:+.2f}%)")
            
        except Exception as e:
            logger.error(f"매도 실행 오류: {e}")
    
    def _print_summary(self):
        """거래 요약 출력"""
        logger.info("=" * 60)
        logger.info("📊 거래 요약")
        logger.info("=" * 60)
        logger.info(f"   총 거래 횟수: {self.state.total_trades}회")
        logger.info(f"   수익 거래: {self.state.winning_trades}회")
        logger.info(f"   손실 거래: {self.state.losing_trades}회")
        
        win_rate = (self.state.winning_trades / 
                   max(self.state.winning_trades + self.state.losing_trades, 1) * 100)
        logger.info(f"   승률: {win_rate:.1f}%")
        logger.info(f"   총 수익: {self.state.total_profit:+,.0f}원")
        logger.info("=" * 60)


async def main():
    """메인 함수"""
    trader = MomentumTrader()
    await trader.start()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n프로그램 종료")
