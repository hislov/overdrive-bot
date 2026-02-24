import os
import yfinance as yf
import pandas as pd
import requests
import threading
import traceback
from flask import Flask, request, jsonify

app = Flask(__name__)

# ==========================================
# 👑 [오너 통제실: API 및 자본 세팅]
# ==========================================
# 보안을 위해 Render 클라우드 환경변수에서 토큰을 불러옵니다.
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = "8744987468" # 오너님 ID 락온 완료

# v4.5 자본 통제 룰
TOTAL_CAPITAL = 43000.0     
TARGET_PROFIT_USD = 600.0   
SLOT_CAPITAL = TOTAL_CAPITAL * 0.80  
MAX_RISK_USD = TOTAL_CAPITAL * 0.015

def send_telegram_message(text):
    """오너님의 스마트폰으로 작전 지시서를 전송합니다."""
    if not TELEGRAM_TOKEN:
        print("🚨 TELEGRAM_TOKEN이 설정되지 않았습니다.")
        return
        
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        print(f"텔레그램 전송 에러: {e}")

def calculate_true_atr(df, period=14):
    try:
        high_low = df['High'] - df['Low']
        high_close = (df['High'] - df['Close'].shift(1)).abs()
        low_close = (df['Low'] - df['Close'].shift(1)).abs()
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        return float(tr.rolling(period).mean().iloc[-1])
    except: return float(df['Close'].iloc[-1]) * 0.02

def process_target_signal(ticker, tv_price):
    """트레이딩뷰가 쏜 타겟을 v4.5 수학 공식으로 분해합니다."""
    print(f"⚡ [요격 명령 수신] 타겟: {ticker} / 1차 엑스레이 스캔 시작...")
    try:
        # 야후 파이낸스에서 일봉 데이터(ATR, 어제 종가 계산용)만 빠르게 가져옵니다.
        df_daily = yf.download(ticker, period="1mo", progress=False)
        if isinstance(df_daily.columns, pd.MultiIndex):
            df_daily = df_daily.xs(ticker, level=1, axis=1)
            
        info = yf.Ticker(ticker).info or {}
        market_cap = float(info.get('marketCap', 0.0))
        
        atr = calculate_true_atr(df_daily)
        yesterday_close = float(df_daily['Close'].iloc[-2]) if len(df_daily) > 1 else tv_price
        entry_price = float(tv_price)
        
        # 1분봉으로 VWAP과 PM_HIGH 계산 (지연이 있더라도 대략적인 값 확보)
        hist_1m = yf.Ticker(ticker).history(period="2d", interval="1m", prepost=True)
        pm_vwap = entry_price
        pm_high = entry_price
        if not hist_1m.empty:
            dates = hist_1m.index.normalize().unique()
            if len(dates) > 0:
                today_data = hist_1m[hist_1m.index.normalize() == dates[-1]]
                if not today_data.empty and today_data['Volume'].sum() > 0:
                    pm_vwap = float((((today_data['High']+today_data['Low']+today_data['Close'])/3)*today_data['Volume']).sum() / today_data['Volume'].sum())
                    pm_high = float(today_data['High'].max())
        
        gap_pct = ((entry_price - yesterday_close) / yesterday_close) * 100 if yesterday_close > 0 else 0.0

        # 🚨 [v4.5 시총 압축 스케일링]
        cap_scale = 1.0
        if market_cap > 100_000_000_000: cap_scale = 0.5
        elif market_cap > 20_000_000_000: cap_scale = 0.7

        entry_2_val = max(entry_price * 0.85, entry_price - (atr * 0.5 * cap_scale))
        avg_entry = (entry_price + entry_2_val) / 2.0
        
        sl_distance = max(atr * cap_scale, avg_entry * 0.01)
        base_hard_stop = avg_entry - sl_distance

        # 🚨 [v4.5 갭 디스카운트 & 천장 계산]
        gap_discount = 1.0
        if gap_pct > 0: gap_discount = max(0.5, 1.0 - (gap_pct / 10.0))
        
        reward_unit = max(atr * 0.8 * cap_scale * gap_discount, avg_entry * 0.008)
        theoretical_ceiling = yesterday_close + (atr * 1.5)
        
        raw_tp1 = avg_entry + reward_unit
        tp1_trigger = min(raw_tp1, theoretical_ceiling * 0.998)
        
        # 🚨 [v4.5 프론트러닝 (프리장 고점 락온)]
        if pm_high > avg_entry and tp1_trigger > pm_high:
            tp1_trigger = max(pm_high * 0.998, avg_entry * 1.005)

        tp2_trigger = min(avg_entry + (reward_unit * 3.0), theoretical_ceiling * 1.01)
        if tp2_trigger <= tp1_trigger: tp2_trigger = tp1_trigger + (avg_entry * 0.005)

        # 켈리 베팅 수량 계산
        pps = tp1_trigger - avg_entry
        rps = avg_entry - base_hard_stop
        
        if rps <= 0: return

        ideal_total_qty = max(1, int(TARGET_PROFIT_USD // pps) + 1) * 2
        qty = min(ideal_total_qty, max(2, int(MAX_RISK_USD // rps)), max(2, int(SLOT_CAPITAL // avg_entry)))
        if qty % 2 != 0: qty -= 1
        if qty < 2: qty = 2
        half_qty = qty // 2
        
        max_total_loss = rps * qty

        def get_offset(price): return max(0.10, price * 0.002)

        # 📱 [텔레그램 메시지 조립]
        msg = f"🚀 *[OVERDRIVE v5.0 타격 명령]*\n"
        msg += f"━━━━━━━━━━━━━━━━━━━━\n"
        msg += f"🎯 *타겟:* `{ticker}` (현재가: ${entry_price:.2f})\n"
        msg += f"📊 당일 갭: +{gap_pct:.2f}% | 스케일링: {cap_scale}x\n"
        msg += f"🛡️ 최대 리스크: -${max_total_loss:,.0f} (통제됨)\n"
        msg += f"━━━━━━━━━━━━━━━━━━━━\n"
        msg += f"🛒 *[MTS OCO 덫 세팅표]*\n\n"
        
        msg += f"🔵 *1차 진입 (지금 즉시)*\n"
        msg += f" ➔ 수량: `{half_qty}주` (시장가/지정가 긁기)\n\n"
        msg += f"🔵 *2차 매복 (미리 깔아두기)*\n"
        msg += f" ➔ 수량: `{half_qty}주` (지정가 `${entry_2_val:.2f}`)\n"
        msg += f"  *(예상 평단가: ${avg_entry:.2f})*\n\n"
        
        msg += f"🔴 *손절 (조건 판매)*\n"
        msg += f" ➔ 조건: `${base_hard_stop:.2f}` 이하 (전량 시장가)\n\n"
        
        msg += f"🟢 *1차 익절 (조건 판매)*\n"
        msg += f" ➔ 조건: `${tp1_trigger:.2f}` 이상\n"
        msg += f" ➔ 주문: `{half_qty}주` (지정가 `${tp1_trigger - get_offset(tp1_trigger):.2f}`)\n\n"
        msg += f"🚀 *2차 런너 (조건 판매)*\n"
        msg += f" ➔ 조건: `${tp2_trigger:.2f}` 이상\n"
        msg += f" ➔ 주문: `{half_qty}주` (지정가 `${tp2_trigger - get_offset(tp2_trigger):.2f}`)\n"
        msg += f"━━━━━━━━━━━━━━━━━━━━\n"
        msg += f"💤 *세팅 완료 후 즉시 폰을 덮고 취침하십시오.*"

        send_telegram_message(msg)
        print(f"✅ [{ticker}] 텔레그램 발송 완료.")

    except Exception as e:
        error_msg = traceback.format_exc()
        print(f"🚨 오류: {error_msg}")
        send_telegram_message(f"🚨 *[{ticker}] 시스템 연산 에러*\n`{e}`")

# ==========================================
# 🌐 [WEBHOOK ENDPOINT : 트레이딩뷰 수신망]
# ==========================================
@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.json
        ticker = data.get("ticker", "").upper()
        price = float(data.get("price", 0.0))
        
        if not ticker or price == 0:
            return jsonify({"status": "error", "message": "Invalid data"}), 400
        
        # 텔레그램 타임아웃 방지를 위해 백그라운드 스레드에서 연산
        threading.Thread(target=process_target_signal, args=(ticker, price)).start()
        
        return jsonify({"status": "success", "message": f"Target {ticker} intercepted"}), 200

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/test', methods=['GET'])
def test_route():
    """오너님 스마트폰 브라우저 모의 테스트용 URL"""
    ticker = request.args.get('ticker', 'NVDA').upper()
    price = float(request.args.get('price', 150.0))
    send_telegram_message(f"🛠️ *[시스템 테스트]* 브라우저에서 `{ticker}` 모의 타격 신호가 수신되었습니다. 덫 연산을 시작합니다...")
    threading.Thread(target=process_target_signal, args=(ticker, price)).start()
    return f"Test signal for {ticker} sent to Telegram. Check your app!", 200

@app.route('/', methods=['GET'])
def index():
    return "👑 OVERDRIVE NEXUS IS ONLINE.", 200

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
