import os
import yfinance as yf
import pandas as pd
import requests
import threading
import io
import time
import re
import matplotlib
matplotlib.use('Agg') # 클라우드 이미지 에러 방지용 헤드리스 모드
import mplfinance as mpf
import PIL.Image
import google.generativeai as genai
from flask import Flask, request

app = Flask(__name__)

# ==========================================
# 👑 [오너 통제실]
# ==========================================
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = "8744987468"
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

TOTAL_CAPITAL = 43000.0     
TARGET_PROFIT_USD = 600.0   
SLOT_CAPITAL = TOTAL_CAPITAL * 0.80  
MAX_RISK_USD = TOTAL_CAPITAL * 0.015

CORE_UNIVERSE = [
    'AAPL','MSFT','NVDA','AMZN','META','GOOGL','TSLA','BRK-B','AVGO','LLY',
    'JPM','UNH','V','XOM','MA','JNJ','PG','HD','COST','MRK','ABBV','CRM','AMD',
    'PLTR','SMCI','MSTR','CRWD','PANW','NFLX','DIS','INTC','CSCO','PEP','KO',
    'WMT','BAC','MCD','LIN','ADBE','TXN','QCOM','AMGN','INTU','IBM','CAT','GE',
    'QQQ','SPY'
]

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

def send_telegram(text):
    if not TELEGRAM_TOKEN: return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"})

def calculate_true_atr(df_high, df_low, df_close, period=14):
    try:
        tr = pd.concat([df_high - df_low, (df_high - df_close.shift(1)).abs(), (df_low - df_close.shift(1)).abs()], axis=1).max(axis=1)
        return float(tr.rolling(period).mean().iloc[-1])
    except: return float(df_close.iloc[-1]) * 0.02

# ==========================================
# 🦅 [전체 시장 자동 스캔 봇 (APEX HUNTER)]
# ==========================================
def execute_auto_hunt():
    send_telegram("🦅 *[OVERDRIVE APEX: 자율 사냥 개시]*\n미국장 전체 데이터 스캔 및 AI 챔피언스 리그를 렌더링합니다. (약 1~2분 소요)")
    try:
        # 1. 일봉 데이터 대량 스캔
        data = yf.download(CORE_UNIVERSE, period="3mo", threads=True, progress=False, prepost=True)
        if isinstance(data.columns, pd.MultiIndex):
            closes, volumes, opens = (data[col] if col in data.columns.levels[0] else data.xs(col, level=1, axis=1) for col in ['Close', 'Volume', 'Open'])
            highs, lows = (data[col] if col in data.columns.levels[0] else data.xs(col, level=1, axis=1) for col in ['High', 'Low'])
        else: return send_telegram("🚨 데이터 로드 에러.")

        qqq_c = closes['QQQ'].dropna()
        qqq_10d = float((qqq_c.iloc[-1] - qqq_c.iloc[-10]) / qqq_c.iloc[-10])
        
        stats_list = []
        df_dict = {}
        
        # 2. 파워 스코어 연산
        for cand in CORE_UNIVERSE:
            if cand == 'QQQ': continue
            try:
                cand_df = pd.DataFrame({'Open': opens[cand], 'High': highs[cand], 'Low': lows[cand], 'Close': closes[cand], 'Volume': volumes[cand]}).dropna()
                if len(cand_df) < 25: continue
                
                rs = float(((cand_df['Close'].iloc[-1] - cand_df['Close'].iloc[-10]) / cand_df['Close'].iloc[-10]) - qqq_10d)
                avg_v = float(cand_df['Volume'].iloc[-11:-1].mean())
                curr_v = float(cand_df['Volume'].iloc[-1])
                v_spike = curr_v / avg_v if avg_v > 0 else 0.0
                
                power_score = (rs + 1.0) * v_spike
                
                df_dict[cand] = cand_df
                stats_list.append({'Ticker': cand, 'Price': float(cand_df['Close'].iloc[-1]), 'Prev_Close': float(cand_df['Close'].iloc[-2]), 'Power_Score': power_score, 'Vol_Spike': v_spike})
            except: continue
            
        stats = pd.DataFrame(stats_list)
        if stats.empty: return send_telegram("🚨 조건에 맞는 종목이 없습니다.")
            
        # 3. 상위 5개 압축 및 1분봉 VWAP 투시
        top_cands = stats.sort_values(by='Power_Score', ascending=False).head(5)
        final_cands = []
        
        for _, row in top_cands.iterrows():
            t = row['Ticker']
            p = row['Price']
            try:
                mcap = yf.Ticker(t).info.get('marketCap', 0.0)
                hist_1m = yf.Ticker(t).history(period="1d", interval="1m", prepost=True)
                vwap_stat = "알수없음"
                if not hist_1m.empty and hist_1m['Volume'].sum() > 0:
                    vwap = float((((hist_1m['High']+hist_1m['Low']+hist_1m['Close'])/3)*hist_1m['Volume']).sum() / hist_1m['Volume'].sum())
                    vwap_stat = "🚨설거지(VWAP하회)" if p < vwap else "✅찐수급(VWAP상회)"
            except: mcap = 0.0; vwap_stat = "에러"
                
            final_cands.append({'Ticker': t, 'Price': p, 'Prev_Close': row['Prev_Close'], 'Power_Score': row['Power_Score'], 'Vol_Spike': row['Vol_Spike'], 'Market_Cap': mcap, 'VWAP_Status': vwap_stat})

        # 4. 제미나이 AI 챔피언스 리그 (최종 1위 선별)
        winner_ticker = final_cands[0]['Ticker']
        insight = "AI 미작동 (파워스코어 1위 강제 지정)"
        
        if GEMINI_API_KEY:
            try:
                contents = ["당신은 수석 퀀트입니다. 아래 5개 차트를 잔혹하게 비교하여 가장 펌핑 확률이 높은 완벽한 1개 종목만 골라주세요. 첫 줄에 무조건 [SELECTED: 티커명] 을 적으세요.\n"]
                images_attached = 0
                for i, cand in enumerate(final_cands):
                    t = cand['Ticker']
                    contents[0] += f"[{i+1}] {t} | 파워스코어: {cand['Power_Score']:.2f} | 예상RVOL: {cand['Vol_Spike']:.1f}x | 수급: {cand['VWAP_Status']}\n"
                    buf = io.BytesIO()
                    try:
                        mpf.plot(df_dict[t][-90:], type='candle', volume=True, style='yahoo', title=f"[{i+1}] {t}", savefig=dict(fname=buf, dpi=60))
                        buf.seek(0)
                        contents.append(f"[{t} 차트]")
                        contents.append(PIL.Image.open(buf))
                        images_attached += 1
                    except: pass
                
                if images_attached > 0:
                    response = genai.GenerativeModel('gemini-2.5-pro').generate_content(contents, generation_config={"temperature": 0.2})
                    text = response.text.strip()
                    match = re.search(r'\[SELECTED:\s*([A-Za-z0-9\-]+)\]', text, re.IGNORECASE)
                    if match: winner_ticker = match.group(1).upper()
                    insight = text
            except Exception as e: insight = f"AI 통신 에러: {e}"

        # 5. OCO 덫 연산 (v4.5)
        winner_data = next((item for item in final_cands if item["Ticker"] == winner_ticker), final_cands[0])
        df_win = df_dict[winner_ticker]
        atr = calculate_true_atr(df_win['High'], df_win['Low'], df_win['Close'])
        
        entry_price = winner_data['Price']
        yesterday_close = winner_data['Prev_Close']
        gap_pct = ((entry_price - yesterday_close) / yesterday_close) * 100 if yesterday_close > 0 else 0.0
        
        # 시총/갭 스케일링
        cap_scale = 0.5 if winner_data['Market_Cap'] > 100_000_000_000 else 0.7 if winner_data['Market_Cap'] > 20_000_000_000 else 1.0
        gap_discount = max(0.5, 1.0 - (gap_pct / 10.0)) if gap_pct > 0 else 1.0

        entry_2_val = entry_price - (atr * 0.5 * cap_scale)
        avg_entry = (entry_price + entry_2_val) / 2.0
        
        sl_distance = max(atr * cap_scale, avg_entry * 0.01)
        base_hard_stop = avg_entry - sl_distance
        
        reward_unit = max(atr * 0.8 * cap_scale * gap_discount, avg_entry * 0.008)
        tp1_trigger = avg_entry + reward_unit
        tp2_trigger = tp1_trigger + (reward_unit * 2.0)
        
        rps = avg_entry - base_hard_stop
        pps = tp1_trigger - avg_entry
        
        ideal_qty = max(1, int(TARGET_PROFIT_USD // pps) + 1) * 2 if pps > 0 else 2
        qty = min(ideal_qty, max(2, int(MAX_RISK_USD // rps)) if rps > 0 else 2, max(2, int(SLOT_CAPITAL // avg_entry)))
        if qty % 2 != 0: qty -= 1
        half_qty = max(1, qty // 2)
        max_total_loss = rps * qty

        def get_offset(price): return max(0.10, price * 0.002)

        # 6. 최종 텔레그램 발송
        msg = f"🏆 *[OVERDRIVE AI: 챔피언스 리그 우승자]*\n"
        msg += f"━━━━━━━━━━━━━━━━━━━━\n"
        msg += f"🎯 *타겟:* `{winner_ticker}` (현재가: ${entry_price:.2f})\n"
        msg += f"📊 팩트: 당일 갭 {gap_pct:+.2f}% | 시총스케일 {cap_scale}x\n"
        msg += f"🛡️ 최대 통제 리스크: -${max_total_loss:,.0f}\n"
        msg += f"━━━━━━━━━━━━━━━━━━━━\n"
        msg += f"🛒 *[MTS OCO 덫 세팅표]*\n\n"
        msg += f"🔵 *1차 진입:* `{half_qty}주` (시장가/지정가 긁기)\n"
        msg += f"🔵 *2차 매복:* `{half_qty}주` (지정가 `${entry_2_val:.2f}`)\n"
        msg += f"🔴 *손절 (전량):* `${base_hard_stop:.2f}` 이하\n"
        msg += f"🟢 *1차 익절:* `{half_qty}주` (지정가 `${tp1_trigger - get_offset(tp1_trigger):.2f}`)\n"
        msg += f"🚀 *2차 런너:* `{half_qty}주` (지정가 `${tp2_trigger - get_offset(tp2_trigger):.2f}`)\n"
        msg += f"━━━━━━━━━━━━━━━━━━━━\n"
        msg += f"🧠 *[AI 심사평]*\n`{insight[:400]}...`\n\n"
        msg += f"💤 *세팅 완료 후 즉시 폰을 덮고 취침하십시오.*"

        send_telegram(msg)
        
    except Exception as e:
        send_telegram(f"🚨 *[사냥 엔진 에러]*\n`{str(e)}`")

# ==========================================
# 🌐 [원격 격발 라우터 (원버튼 스위치)]
# ==========================================
@app.route('/hunt', methods=['GET'])
def trigger_hunt():
    """스마트폰 브라우저로 접속 시 자동 사냥 시작 (타임아웃 방지용 스레드)"""
    threading.Thread(target=execute_auto_hunt).start()
    return "🦅 OVERDRIVE AUTONOMOUS HUNTER INITIATED. CHECK TELEGRAM IN 1-2 MIN.", 200

@app.route('/', methods=['GET'])
def index():
    return "👑 OVERDRIVE NEXUS IS ONLINE.", 200

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
