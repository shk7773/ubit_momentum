#!/usr/bin/env python3
"""
최소 주문 금액으로 매수/매도 테스트
업비트 최소 주문 금액: 5,000원
"""

import os
import time
import uuid
import hashlib
from urllib.parse import urlencode

import jwt
import requests
from dotenv import load_dotenv

# 환경변수 로드
load_dotenv()

ACCESS_KEY = os.getenv("UPBIT_ACCESS_KEY")
SECRET_KEY = os.getenv("UPBIT_SECRET_KEY")

BASE_URL = "https://api.upbit.com/v1"
MARKET = "KRW-BTC"
TEST_AMOUNT = 6000  # 최소 주문 금액보다 충분히 높게 설정


def generate_jwt(query=None):
    """JWT 토큰 생성"""
    payload = {
        'access_key': ACCESS_KEY,
        'nonce': str(uuid.uuid4()),
    }
    
    if query:
        query_string = urlencode(query).encode()
        m = hashlib.sha512()
        m.update(query_string)
        payload['query_hash'] = m.hexdigest()
        payload['query_hash_alg'] = 'SHA512'
        
    return jwt.encode(payload, SECRET_KEY)


def api_request(method, endpoint, params=None, data=None):
    """API 요청"""
    url = f"{BASE_URL}{endpoint}"
    headers = {}
    
    if params or data:
        token = generate_jwt(params or data)
        headers['Authorization'] = f"Bearer {token}"
    elif endpoint.startswith('/orders') or endpoint == '/accounts':
        token = generate_jwt()
        headers['Authorization'] = f"Bearer {token}"
    
    try:
        if method == 'GET':
            response = requests.get(url, params=params, headers=headers)
        elif method == 'POST':
            headers['Content-Type'] = 'application/json; charset=utf-8'
            response = requests.post(url, json=data, headers=headers)
        elif method == 'DELETE':
            response = requests.delete(url, params=params, headers=headers)
        
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"❌ API 오류: {e}")
        if hasattr(e, 'response') and e.response:
            print(f"   응답: {e.response.text}")
        return None


def get_current_price():
    """현재가 조회"""
    result = api_request('GET', '/ticker', params={'markets': MARKET})
    if result:
        return result[0]['trade_price']
    return None


def get_balance(currency):
    """잔고 조회"""
    result = api_request('GET', '/accounts')
    if result:
        for acc in result:
            if acc['currency'] == currency:
                return float(acc['balance']), float(acc['locked'])
    return 0, 0


def market_buy(amount):
    """시장가 매수"""
    print(f"\n🛒 시장가 매수 주문 (금액: {amount:,}원)")
    
    data = {
        'market': MARKET,
        'side': 'bid',
        'ord_type': 'price',  # 시장가 매수
        'price': str(amount)
    }
    
    result = api_request('POST', '/orders', data=data)
    if result:
        print(f"   ✅ 주문 UUID: {result['uuid']}")
        print(f"   📌 상태: {result['state']}")
        return result['uuid']
    return None


def market_sell(volume):
    """시장가 매도"""
    print(f"\n💵 시장가 매도 주문 (수량: {volume:.8f} BTC)")
    
    data = {
        'market': MARKET,
        'side': 'ask',
        'ord_type': 'market',  # 시장가 매도
        'volume': str(volume)
    }
    
    result = api_request('POST', '/orders', data=data)
    if result:
        print(f"   ✅ 주문 UUID: {result['uuid']}")
        print(f"   📌 상태: {result['state']}")
        return result['uuid']
    return None


def get_order(uuid_val):
    """주문 조회"""
    return api_request('GET', '/order', params={'uuid': uuid_val})


def wait_for_order(uuid_val, timeout=10):
    """주문 체결 대기 (시장가 주문은 즉시 체결됨)"""
    print(f"   ⏳ 체결 확인 중...")
    for i in range(timeout):
        time.sleep(0.5)
        order = get_order(uuid_val)
        if order:
            executed = float(order.get('executed_volume', 0))
            state = order['state']
            
            # 시장가 주문: executed_volume > 0이면 체결된 것
            if executed > 0:
                if state == 'done':
                    print(f"   ✅ 전량 체결 완료!")
                elif state == 'cancel':
                    print(f"   ✅ 부분 체결 후 잔여 취소 (정상)")
                return order
            elif state == 'cancel':
                print(f"   ❌ 미체결 취소")
                return order
            elif state == 'done':
                print(f"   ✅ 체결 완료!")
                return order
    print(f"   ⚠️ 타임아웃 - 현재 상태 반환")
    return get_order(uuid_val)


def main():
    print("=" * 60)
    print("🧪 업비트 최소 금액 매수/매도 테스트")
    print("=" * 60)
    
    # 1. 현재 상태 확인
    print("\n📊 현재 상태 확인")
    current_price = get_current_price()
    if not current_price:
        print("❌ 현재가 조회 실패")
        return
    print(f"   BTC 현재가: {current_price:,.0f}원")
    
    krw_balance, krw_locked = get_balance('KRW')
    btc_balance, btc_locked = get_balance('BTC')
    print(f"   KRW 잔고: {krw_balance:,.0f}원 (잠김: {krw_locked:,.0f}원)")
    print(f"   BTC 잔고: {btc_balance:.8f} (잠김: {btc_locked:.8f})")
    
    if krw_balance < TEST_AMOUNT:
        print(f"\n❌ 잔고 부족! 최소 {TEST_AMOUNT:,}원 필요")
        return
    
    # 2. 시장가 매수 테스트
    print("\n" + "-" * 60)
    print("📍 STEP 1: 시장가 매수 테스트")
    print("-" * 60)
    
    buy_uuid = market_buy(TEST_AMOUNT)
    if not buy_uuid:
        print("❌ 매수 주문 실패")
        return
    
    buy_order = wait_for_order(buy_uuid)
    if not buy_order:
        print("❌ 매수 주문 조회 실패")
        return
    
    executed_volume = float(buy_order.get('executed_volume', 0))
    if executed_volume <= 0:
        print("❌ 매수 체결 없음")
        return
    
    # 체결 정보
    paid_fee = float(buy_order.get('paid_fee', 0))
    print(f"\n   📋 매수 체결 정보:")
    print(f"      체결 수량: {executed_volume:.8f} BTC")
    print(f"      수수료: {paid_fee:.2f}원")
    
    # 잔고 확인
    time.sleep(1)
    btc_balance_after, _ = get_balance('BTC')
    print(f"      BTC 잔고: {btc_balance:.8f} → {btc_balance_after:.8f}")
    
    # 3. 시장가 매도 테스트
    print("\n" + "-" * 60)
    print("📍 STEP 2: 시장가 매도 테스트")
    print("-" * 60)
    
    # 매수한 수량 전량 매도
    sell_volume = executed_volume
    sell_uuid = market_sell(sell_volume)
    if not sell_uuid:
        print("❌ 매도 주문 실패")
        return
    
    sell_order = wait_for_order(sell_uuid)
    if not sell_order:
        print("❌ 매도 주문 조회 실패")
        return
    
    sell_executed = float(sell_order.get('executed_volume', 0))
    if sell_executed <= 0:
        print("❌ 매도 체결 없음")
        return
    
    # 체결 정보
    sell_fee = float(sell_order.get('paid_fee', 0))
    print(f"\n   📋 매도 체결 정보:")
    print(f"      체결 수량: {sell_executed:.8f} BTC")
    print(f"      수수료: {sell_fee:.2f}원")
    
    # 최종 잔고 확인
    print("\n" + "-" * 60)
    print("📍 STEP 3: 테스트 결과")
    print("-" * 60)
    
    time.sleep(1)
    krw_final, _ = get_balance('KRW')
    btc_final, _ = get_balance('BTC')
    
    total_fee = paid_fee + sell_fee
    krw_diff = krw_final - krw_balance
    
    print(f"\n   💰 최종 잔고:")
    print(f"      KRW: {krw_balance:,.0f} → {krw_final:,.0f}원 (차이: {krw_diff:+,.0f}원)")
    print(f"      BTC: {btc_balance:.8f} → {btc_final:.8f}")
    print(f"\n   📊 비용 분석:")
    print(f"      총 수수료: {total_fee:.2f}원")
    print(f"      슬리피지 등 기타: {abs(krw_diff) - total_fee:.2f}원")
    
    print("\n" + "=" * 60)
    print("✅ 테스트 완료!")
    print("=" * 60)


if __name__ == "__main__":
    main()
