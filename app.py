import os
import threading
import yfinance as yf
import pandas as pd
import numpy as np
import requests
import io
import csv
import time
import re
from datetime import datetime
import pytz
import json
import warnings
import matplotlib
matplotlib.use('Agg') # 클라우드 이미지 에러 방지용
import mplfinance as mpf
import PIL.Image
import google.generativeai as genai
import concurrent.futures
from flask import Flask, request, jsonify

warnings.filterwarnings('ignore')

app = Flask(__name__)

# ==========================================
# 🔑 [API 환경 변수 세팅]
# ==========================================
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = "8744987468" # 오너님 고유 ID
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
KIS_APP_KEY = os.environ.get("KIS_APP_KEY", "")
KIS_APP_SECRET = os.environ.get("KIS_APP_SECRET", "")

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

KIS_URL_BASE = "https://openapivts.koreainvestment.com:29443"

# ==========================================
# 👑 [오너 통제 변수 세팅 (v4.5 원본)]
# ==========================================
TOTAL_CAPITAL = 43000.0     
TARGET_PROFIT_USD = 600.0   
SLOT_CAPITAL = TOTAL_CAPITAL * 0.80  
MAX_RISK_USD = TOTAL_CAPITAL * 0.015
MANUAL_TARGET = "" 
ACTUAL_ENTRY_PRICE = 0.0
FAILED_TICKERS = []
EXCLUDE_TICKERS = ['FI'] 
MAX_GAP_UP = 0.15
STRICT_FAIL_CLOSED = True   
VIX_KILL_SWITCH = 25.0  

CORE_UNIVERSE = [
    'AAPL','MSFT','NVDA','AMZN','META','GOOGL','TSLA','BRK-B','AVGO','LLY',
    'JPM','UNH','V','XOM','MA','JNJ','PG','HD','COST','MRK','ABBV','CRM','AMD',
    'PLTR','SMCI','MSTR','CRWD','PANW','NFLX','DIS','INTC','CSCO','PEP','KO',
    'WMT','BAC','MCD','LIN','ADBE','TXN','QCOM','AMGN','INTU','IBM','CAT','GE',
    'NOW','ISRG','SPGI','UBER','BA','HON','AXP','GS','RTX','BKNG','ELV','SYK',
    'LMT','T','BLK','MDT','TJX','PGR','C','BSX','VRTX','REGN','ADP','MMC','CB',
    'CVS','CI','BMY','KLAC','MU','DE','GILD','ADI','ZTS','MELI','LRCX',
    'SNPS','CDNS','PYPL','CMCSA','TMUS','AMAT','GPN','ICE','SO','DUK','TGT',
    'ITW','NOC','BDX','EOG','SLB','MPC','OXY','COP','QQQ','SPY','DIA','IWM'
]
INVERSE_UNIVERSE = ['SQQQ', 'SOXS', 'SPXU', 'SDOW', 'TECS', 'TZA', 'FAZ', 'LABD', 'SRTY']

LOG_DIR = './OVERDRIVE_DATA'
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, "overdrive_battle_log.csv")
STATE_FILE = os.path.join(LOG_DIR, "overdrive_state.json")

# ==========================================
# 📡 [TELEGRAM TERMINAL SYSTEM]
# ==========================================
telegram_log = ""

def t_print(*args):
    """기존 터미널의 print()를 가로채어 텔레그램으로 보낼 준비를 합니다."""
    global telegram_log
    msg = " ".join(map(str, args))
    telegram_log += msg + "\n"
    print(msg) # Render 로그용

def flush_telegram():
    """쌓인 터미널 출력물을 텔레그램으로 발사합니다."""
    global telegram_log
    if not TELEGRAM_TOKEN or not telegram_log: return
    
    # HTML 변환 및 <pre> 태그로 터미널 고정폭 폰트 적용
    safe_text = telegram_log.replace('<', '&lt;').replace('>', '&gt;')
    parts = [safe_text[i:i+3500] for i in range(0, len(safe_text), 3500)]
    for p in parts:
        try:
            requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", 
                          json={"chat_id": TELEGRAM_CHAT_ID, "text": f"<pre>{p}</pre>", "parse_mode": "HTML"}, timeout=10)
        except Exception as e:
            print(f"텔레그램 전송 에러: {e}")
    telegram_log = ""

# ==========================================
# 💾 [CORE FUNCTIONS (v4.5 원본)]
# ==========================================
def save_blackbox_log(log_data):
    file_exists = os.path.isfile(LOG_FILE)
    try:
        with open(LOG_FILE, mode='a', encoding='utf-8-sig', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=log_data.keys())
            if not file_exists: writer.writeheader()
            writer.writerow(log_data)
        t_print(f"\n💾 [BLACKBOX LOGGED] 오늘의 전투 데이터가 보존되었습니다.")
    except Exception as e: t_print(f"\n⚠️ [BLACKBOX ERROR] 로그 저장 실패: {e}")

def load_failed_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r') as f: return json.load(f).get("FAILED_TICKERS", [])
        except: pass
    return []

def save_failed_state(tickers):
    try:
        with open(STATE_FILE, 'w') as f: json.dump({"FAILED_TICKERS": tickers}, f)
    except: pass

def print_overdrive_timeline():
    t_print("\n" + "="*80)
    t_print("  ██████╗ ██╗   ██╗███████╗██████╗ ██████╗ ██████╗ ██╗██╗   ██╗███████╗")
    t_print(" ██╔═══██╗██║   ██║██╔════╝██╔══██╗██╔══██╗██╔══██╗██║██║   ██║██╔════╝")
    t_print(" ██║   ██║██║   ██║█████╗  ██████╔╝██║  ██║██████╔╝██║██║   ██║█████╗  ")
    t_print(" ██║   ██║╚██╗ ██╔╝██╔══╝  ██╔══██╗██║  ██║██╔══██╗██║╚██╗ ██╔╝██╔══╝  ")
    t_print(" ╚██████╔╝ ╚████╔╝ ███████╗██║  ██║██████╔╝██║  ██║██║ ╚████╔╝ ███████╗")
    t_print("  ╚═════╝   ╚═══╝  ╚══════╝╚═╝  ╚═╝╚═════╝ ╚═╝  ╚═╝╚═╝  ╚═══╝  ╚══════╝")
    t_print("      [ V E R S I O N : A P E X - LIMIT UNLOCKED + ORACLE PREDICTOR ]")
    t_print("="*80)
    t_print(" 👑 [NIGHTFALL PROTOCOL: 수석 아키텍트 절대 지침]")
    t_print("--------------------------------------------------------------------------------")
    t_print(" 🔥 🕚 밤 11:05 ~ 11:10 (스텔스 스캔) : 봇 실행 최적기! 기관 VWAP 세팅 및 갭 판독 완료.")
    t_print(" ⚙️ 🕦 밤 11:15 (덫 세팅 완료)   : 기계가 뱉어낸 'OVERDRIVE 조준표'를 앱에 100% 카피.")
    t_print(" 💤 🕦 밤 11:30 (본장 개장)      : 광기의 호가창을 무시하고 즉시 스마트폰 덮고 취침!")
    t_print(" ⏰ 🕟 새벽 05:20 ~ 05:40 (파워아워 심판): 알람 기상! 본전 아래면 당일 컷 / 본전 위면 무위험 스윙 셋업!")
    t_print("="*80 + "\n")

def get_market_status():
    tz = pytz.timezone('US/Eastern')
    now = datetime.now(tz)
    market_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
    market_close = now.replace(hour=16, minute=0, second=0, microsecond=0)
    is_regular = market_open <= now < market_close
    is_pre = now < market_open
    if is_pre:
        pre_start = now.replace(hour=4, minute=0, second=0, microsecond=0)
        elapsed = max(1.0, (now - pre_start).total_seconds() / 60.0)
        progress = max(0.01, (elapsed / 330.0) * 0.15) 
    elif now >= market_close: progress = 1.0  
    else:
        elapsed = (now - market_open).total_seconds() / 60.0
        progress = max(0.05, elapsed / 390.0)
    return is_regular, is_pre, progress

def get_macro_environment():
    try:
        data = yf.download(["^VIX", "^TNX"], period="5d", progress=False)
        if isinstance(data.columns, pd.MultiIndex):
            closes = data['Close'] if 'Close' in data.columns.levels[0] else data.xs('Close', level=1, axis=1)
        else:
            closes = data['Close'] if 'Close' in data.columns else pd.DataFrame()
        closes = closes.dropna(how='all')
        if not closes.empty and '^VIX' in closes.columns:
            return float(closes['^VIX'].dropna().iloc[-1]), float(closes['^TNX'].dropna().iloc[-1]) if '^TNX' in closes.columns else 4.0
    except: pass
    return (20.0, 4.0)

def calculate_true_atr(df_high, df_low, df_close, period=14):
    try:
        if len(df_close) < period + 1: return float(df_close.iloc[-1]) * 0.02
        tr = pd.concat([df_high - df_low, (df_high - df_close.shift(1)).abs(), (df_low - df_close.shift(1)).abs()], axis=1).max(axis=1)
        return float(tr.rolling(period).mean().iloc[-1])
    except: return float(df_close.iloc[-1]) * 0.02

def ask_gemini_champions_league(candidates_info, df_dict, vix, is_doomsday):
    if not GEMINI_API_KEY: 
        return None, "[REJECTED]\n🚨 API 키 누락. (Fail-Closed)"
        
    t_print(f"      👁️ [OVERDRIVE Vision] 상위 {len(candidates_info)}개 종목 캔들 차트 렌더링 및 챔피언스 리그 준비 중...")
    
    mode_text = "🔥 [DOOMSDAY 인버스 데스매치]" if is_doomsday else "[OVERDRIVE: APEX 10대 후보]"
    contents = [f"당신은 월스트리트 최상위 퀀트 트레이더입니다. VIX 공포지수: {vix:.2f}\n\n{mode_text}\n"]
    
    images_attached = 0
    for i, cand in enumerate(candidates_info):
        t = cand['Ticker']
        vwap_stat = cand.get('VWAP_Status', '')
        contents[0] += f"[{i+1}번 후보] {t} | 파워 스코어: {cand['Power_Score']:.2f} | 예상 RVOL: {cand['Vol_Spike']:.1f}배 | 수급판독: {vwap_stat}\n"
        
        buf = io.BytesIO()
        try:
            mpf.plot(df_dict[t][-90:], type='candle', volume=True, style='yahoo', title=f"[{i+1}] {t}", savefig=dict(fname=buf, dpi=60))
            buf.seek(0)
            contents.append(f"[{i+1}번 후보: {t} 차트]")
            contents.append(PIL.Image.open(buf))
            images_attached += 1
        except Exception: pass
            
    contents.append(f"""
    [데스매치 심사 명령]
    위 첨부된 {images_attached}개의 차트들을 서로 **가장 엄격하고 잔혹하게 비교(Compare)** 하십시오.
    - 저항 매물대가 두터운 차트는 즉각 탈락시키십시오.
    - 윗꼬리가 길거나 이미 고점에서 하락 반전하는 차트(설거지 패턴)는 무조건 거르십시오.
    - 특히 텍스트 데이터에 🚨설거지(VWAP하회) 경고가 떠있는 차트는 세력의 함정이니 1순위로 탈락시키십시오.
    - 가장 완벽한 당일 펌핑 셋업 단 1개(우승자)만 골라내십시오.
    
    [출력 형식]
    1. 첫 줄은 무조건 **[SELECTED: 티커명]** 으로 작성하십시오. (예: [SELECTED: TSLA])
    2. 두 번째 줄부터 팩트 위주로 1위 선정 이유 및 경쟁자 탈락 이유를 짧게 브리핑하십시오.
    
    만약 모든 차트가 위험해 보인다면 주저하지 말고 첫 줄에 [REJECTED]를 적어 자본을 보호하십시오.
    """)
    
    if images_attached == 0: return None, "[REJECTED]\n모든 차트 렌더링 실패."

    t_print(f"      🧠 [APEX Engine] 제미나이(Gemini 2.5 Pro) 코어가 {images_attached}개 차트를 스캔하며 {images_attached-1}마리의 목을 치고 있습니다. (약 10~15초 소요)...")
    
    for attempt in range(3):
        try:
            model = genai.GenerativeModel('gemini-2.5-pro')
            response = model.generate_content(contents, generation_config={"temperature": 0.2})
            text = response.text.strip()
            
            match = re.search(r'\[SELECTED:\s*([A-Za-z0-9\-]+)\]', text, re.IGNORECASE)
            if match:
                winner = match.group(1).upper()
                return winner, f"[CHAMPIONS LEAGUE WINNER]\n{text}"
            elif "[REJECTED]" in text.upper():
                return None, text
            else:
                return None, f"[SYSTEM WARNING] AI 형식 오류.\n{text}"
        except Exception as e: 
            if attempt < 2: 
                wait_time = 2 ** attempt
                t_print(f"      ⏳ [AI 트래픽 잼 감지] 서버 병목. {wait_time}초 대기 후 강제 돌파를 재시도합니다... ({attempt+1}/3)")
                time.sleep(wait_time)
            else:
                return None, f"[REJECTED]\n🚨 AI 10차트 처리 과부하 최종 에러 ({e})"

def ask_gemini_mindset_coach(ticker, target_profit, max_loss, qty, avg_entry, is_second_bullet, is_doomsday):
    if not GEMINI_API_KEY: return "⚠️ [심리 코치 AI 연결 실패] 기계처럼 매매하십시오."
    prompt = f"당신은 월스트리트 수석 심리 통제관입니다. 오너가 1순위 타겟({ticker}) 진입을 앞두고 있습니다. 기계적 하드스탑 최대 리스크: ${max_loss:,.0f}. 오너가 [05:20 기상/OCO 오차없음/수면 매매] 3가지 룰을 지키도록 뼈 때리게 경고하십시오."
    try: return genai.GenerativeModel('gemini-2.5-pro').generate_content(prompt, generation_config={"temperature": 0.7}).text.strip()
    except: return "⚠️ 룰을 지키십시오."

def get_market_universe():
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        sp500 = pd.read_html(io.StringIO(requests.get('https://en.wikipedia.org/wiki/List_of_S%26P_500_companies', headers=headers, timeout=5).text))[0]['Symbol'].str.replace('.', '-', regex=False).tolist()
        ndx_tables = pd.read_html(io.StringIO(requests.get('https://en.wikipedia.org/wiki/Nasdaq-100', headers=headers, timeout=5).text))
        for df in ndx_tables:
            if 'Ticker' in df.columns: return list(set(sp500 + df['Ticker'].str.replace('.', '-', regex=False).tolist() + CORE_UNIVERSE))
        return list(set(sp500 + CORE_UNIVERSE))
    except: return list(set(CORE_UNIVERSE))

def overdrive_apex_execution():
    global telegram_log
    telegram_log = "" # 통신 시작 시 버퍼 초기화
    print_overdrive_timeline()

    manual_ticker = MANUAL_TARGET.strip().upper()
    df_dict = {}  
    
    runtime_failed = [t.upper() for t in FAILED_TICKERS if t.strip()]
    if runtime_failed: save_failed_state(runtime_failed)
    else: saved_failed_list = load_failed_state()
        
    total_exclude = list(set(saved_failed_list + [t.upper() for t in EXCLUDE_TICKERS if t.strip()]))
    is_second_bullet = len(saved_failed_list) > 0

    vix, tnx = get_macro_environment()
    
    is_doomsday = False
    if vix >= VIX_KILL_SWITCH and not manual_ticker:
        is_doomsday = True
        t_print("\n" + "🩸"*40)
        t_print(f" 🚨 [DOOMSDAY MODE ACTIVATED] 현재 VIX: {vix:.2f} (임계치 {VIX_KILL_SWITCH} 돌파)")
        t_print(" 🚨 나스닥 폭락 감지! 하락장을 찢는 [인버스 역회전 모터]를 즉시 기동합니다.")
        t_print("🩸"*40 + "\n")

    if is_second_bullet: t_print(f"\n🔥 🩸 [SECOND SHOT 발동] 패배 종목 {saved_failed_list} 배제 완료. 2순위를 스캔합니다!\n")
    else: t_print(f"🦅 [OVERDRIVE: APEX] {'DOOMSDAY 인버스 엔진' if is_doomsday else '메인 롱 엔진'} 기동 중...")
            
    t_print("=====================================================================\n")

    is_regular_market, is_pre_market, progress_ratio = get_market_status()

    if is_doomsday:
        t_print(f"🔍 [DOOMSDAY 헌팅 모드] 인버스(숏) ETF 대상 '파워 스코어' 스캔 중...\n")
        tickers = [t for t in INVERSE_UNIVERSE if t not in total_exclude] + ['QQQ']
    else:
        t_print(f"🔍 [오토 헌팅 모드] 미국장 전체 대상 1차 예선 스캔 중...\n")
        tickers = [t for t in get_market_universe() if t not in total_exclude] + ['QQQ']

    data = yf.download(tickers, period="3mo", threads=True, progress=False, prepost=True)
    if data.empty: 
        t_print("🚨 [SYSTEM ERROR] 야후 파이낸스에서 데이터를 가져오지 못했습니다.")
        flush_telegram()
        return

    if isinstance(data.columns, pd.MultiIndex):
        closes, volumes, opens = (data[col] if col in data.columns.levels[0] else data.xs(col, level=1, axis=1) for col in ['Close', 'Volume', 'Open'])
        highs, lows = (data[col] if col in data.columns.levels[0] else data.xs(col, level=1, axis=1) for col in ['High', 'Low'])
    else: 
        t_name = tickers[0] if tickers else "UNKNOWN"
        closes, volumes, opens, highs, lows = (pd.DataFrame({t_name: data[col]}) if col in data.columns else pd.DataFrame() for col in ['Close', 'Volume', 'Open', 'High', 'Low'])
    
    try:
        qqq_c = closes['QQQ'].dropna()
        qqq_10d, qqq_20d = float((qqq_c.iloc[-1] - qqq_c.iloc[-10]) / qqq_c.iloc[-10]), float((qqq_c.iloc[-1] - qqq_c.iloc[-20]) / qqq_c.iloc[-20])
    except: qqq_10d, qqq_20d = 0.0, 0.0

    stats_list = []
    t1_vol_req, t1_rs_req = (1.2, -0.05) if is_doomsday else ((2.0, 0.05) if vix >= 20.0 else (1.5, 0.0))

    for cand in tickers:
        if cand == 'QQQ' or cand not in closes.columns or cand not in opens.columns: continue
        cand_df = pd.DataFrame({'Open': opens[cand], 'High': highs[cand], 'Low': lows[cand], 'Close': closes[cand], 'Volume': volumes[cand]}).dropna()
        if len(cand_df) < 25: continue
        
        cand_df = cand_df[~cand_df.index.duplicated(keep='last')].astype(float)
        cand_df.index = pd.to_datetime(cand_df.index)
        
        try:
            comp_rs = (float(((cand_df['Close'].iloc[-1] - cand_df['Close'].iloc[-10]) / cand_df['Close'].iloc[-10]) - qqq_10d) * 0.6) + (float(((cand_df['Close'].iloc[-1] - cand_df['Close'].iloc[-20]) / cand_df['Close'].iloc[-20]) - qqq_20d) * 0.4)
            avg_v, curr_v = float(cand_df['Volume'].iloc[-11:-1].mean()), float(cand_df['Volume'].iloc[-1])
            
            v_spike = 0.0 if (is_pre_market and curr_v < 50000) else (curr_v / progress_ratio) / avg_v if avg_v > 0 else 0.0
            power_score = (comp_rs + 1.0) * v_spike
            
            sma20 = float(cand_df['Close'].rolling(20).mean().iloc[-1])
            today_open = float(cand_df['Open'].iloc[-1]) if cand_df.index[-1].date() == cand_df.index[-1].date() else float(cand_df['Close'].iloc[-1])
            prev_close = float(cand_df['Close'].iloc[-2])
            t_gap = float((today_open - prev_close) / prev_close) * 100 if prev_close > 0 else 0.0
            
            df_dict[cand] = cand_df
            stats_list.append({'Ticker': cand, 'Price': cand_df['Close'].iloc[-1], 'Prev_Close': prev_close, 'RS': comp_rs, 'Vol_Spike': v_spike, 'SMA20': sma20, 'True_Gap': t_gap, 'Basic_Power_Score': power_score})
        except: continue
        
    stats = pd.DataFrame(stats_list)
    if not stats.empty: stats = stats.set_index('Ticker')

    valid_stocks = pd.DataFrame()
    for f in [{"desc": "1단계", "spike": t1_vol_req, "rs": t1_rs_req, "gap": MAX_GAP_UP*100, "trend": not is_doomsday}, {"desc": "2단계", "spike": 0.8, "rs": -0.05, "gap": 20.0, "trend": False}]:
        if stats.empty: break
        passed = stats[(stats['Price'] >= 5.0) & (stats['Price'] <= 1500.0) & (stats['Vol_Spike'] >= f['spike']) & (stats['RS'] >= f['rs']) & (stats['True_Gap'] < f['gap']) & ((stats['Price'] > stats['SMA20']) if f['trend'] else True)]
        if not passed.empty: valid_stocks = pd.concat([valid_stocks, passed.drop('QQQ', errors='ignore')]).drop_duplicates()
        if len(valid_stocks) >= (15 if not is_doomsday else 5): break

    if valid_stocks.empty: 
        t_print("\n🚨 [SYSTEM SHUTDOWN] 오늘 수급 요건을 충족하는 타겟이 없습니다.")
        flush_telegram()
        return

    pre_candidates = valid_stocks.sort_values(by='Basic_Power_Score', ascending=False).head(20)
    
    t_print("\n   🔍 [Phase 2.5] 상위 20개 종목 1분봉 엑스레이 및 3중 페널티(VWAP/Cap/Gap) 스캔 중...")
    
    def deep_scan(ticker, base_score, prev_close, curr_price):
        try:
            info = yf.Ticker(ticker).info or {}
            hist_1m = yf.Ticker(ticker).history(period="3d", interval="1m", prepost=True)
            
            mcap = info.get('marketCap', 0.0)
            gap_pct = ((curr_price - prev_close) / prev_close) * 100 if prev_close > 0 else 0.0
            pm_vwap, pm_high = 0.0, curr_price
            
            if not hist_1m.empty and 'Volume' in hist_1m.columns:
                dates = hist_1m.index.normalize().unique()
                if len(dates) > 0:
                    target_date = dates[-1]
                    today_data = hist_1m[hist_1m.index.normalize() == target_date]
                    if (today_data.empty or today_data['Volume'].sum() <= 0) and len(dates) > 1:
                        target_date = dates[-2]
                        today_data = hist_1m[hist_1m.index.normalize() == target_date]
                        
                    if not today_data.empty and today_data['Volume'].sum() > 0:
                        pm_vwap = (((today_data['High']+today_data['Low']+today_data['Close'])/3)*today_data['Volume']).sum() / today_data['Volume'].sum()
                        pm_high = float(today_data['High'].max())
            
            penalty = 1.0
            if mcap > 100_000_000_000: penalty *= 0.4
            elif mcap > 50_000_000_000: penalty *= 0.7
            if gap_pct > 3.0: penalty *= max(0.2, 3.0 / gap_pct)
            
            vwap_status = "알수없음"
            if pm_vwap > 0:
                if curr_price < pm_vwap: 
                    penalty *= 0.2
                    vwap_status = "🚨설거지(VWAP하회)"
                else: 
                    penalty *= 1.2
                    vwap_status = "✅찐수급(VWAP상회)"
                    
            final_score = base_score * penalty
            return {'Ticker': ticker, 'Power_Score': final_score, 'Market_Cap': mcap, 'Gap_Pct': gap_pct, 'PM_VWAP': pm_vwap, 'PM_High': pm_high, 'VWAP_Status': vwap_status}
        except:
            return {'Ticker': ticker, 'Power_Score': base_score, 'Market_Cap': 0.0, 'Gap_Pct': 0.0, 'PM_VWAP': curr_price, 'PM_High': curr_price, 'VWAP_Status': '에러'}

    deep_results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futs = []
        for cand in pre_candidates.index:
            base_s = float(pre_candidates.loc[cand, 'Basic_Power_Score'])
            pc = float(pre_candidates.loc[cand, 'Prev_Close'])
            cp = float(pre_candidates.loc[cand, 'Price'])
            futs.append(executor.submit(deep_scan, cand, base_s, pc, cp))
        for f in concurrent.futures.as_completed(futs):
            deep_results.append(f.result())
            
    deep_df = pd.DataFrame(deep_results).set_index('Ticker')
    final_candidates = pre_candidates.join(deep_df[['Power_Score', 'Market_Cap', 'Gap_Pct', 'PM_VWAP', 'PM_High', 'VWAP_Status']])
    top_candidates = final_candidates.sort_values(by='Power_Score', ascending=False).head(10)
    
    fallback_target = top_candidates.index[0]
    fallback_rs = float(top_candidates.iloc[0]['RS'])

    t_print(f"\n👑 [{'DOOMSDAY 엔진' if is_doomsday else 'OVERDRIVE APEX'} 가동] 정예 10강 챔피언스 리그 비주얼 검증 (페널티 반영됨)")
    candidates_info = []
    for cand in top_candidates.index:
        c_rs, c_spike, c_power = float(top_candidates.loc[cand, 'RS']), float(top_candidates.loc[cand, 'Vol_Spike']), float(top_candidates.loc[cand, 'Power_Score'])
        vwap_stat = str(top_candidates.loc[cand, 'VWAP_Status'])
        t_print(f"▶️ [후보: {cand:<5}] 파워 스코어: {c_power:>5.2f} (RVOL: {c_spike:>4.1f}배 | 수급판독: {vwap_stat})")
        candidates_info.append({'Ticker': cand, 'RS': c_rs, 'Vol_Spike': c_spike, 'Power_Score': c_power, 'VWAP_Status': vwap_stat})
        
    winner_ticker, insight = ask_gemini_champions_league(candidates_info, df_dict, vix, is_doomsday)
    
    if STRICT_FAIL_CLOSED and "[REJECTED]" in insight:
        t_print(f"\n🚨 [SYSTEM HALT] AI가 모든 후보를 거부했거나 에러가 발생했습니다:\n{insight}")
        flush_telegram()
        return
        
    if winner_ticker and winner_ticker in top_candidates.index:
        final_target = winner_ticker
        final_rs = float(top_candidates.loc[winner_ticker, 'RS'])
        final_vol_spike = float(top_candidates.loc[winner_ticker, 'Vol_Spike'])
        final_power_score = float(top_candidates.loc[winner_ticker, 'Power_Score'])
        pm_vwap_val = float(top_candidates.loc[winner_ticker, 'PM_VWAP'])
        pm_high_val = float(top_candidates.loc[winner_ticker, 'PM_High'])
        gap_pct_val = float(top_candidates.loc[winner_ticker, 'Gap_Pct'])
        market_cap_val = float(top_candidates.loc[winner_ticker, 'Market_Cap'])
        final_insight = insight
        t_print(f"\n   🏆 [OVERDRIVE 최종 타겟 확정] >>> {final_target} <<<\n")
    else:
        final_target, final_rs = fallback_target, fallback_rs
        final_vol_spike = float(top_candidates.iloc[0]['Vol_Spike'])
        final_power_score = float(top_candidates.iloc[0]['Power_Score'])
        pm_vwap_val = float(top_candidates.iloc[0]['PM_VWAP'])
        pm_high_val = float(top_candidates.iloc[0]['PM_High'])
        gap_pct_val = float(top_candidates.iloc[0]['Gap_Pct'])
        market_cap_val = float(top_candidates.iloc[0]['Market_Cap'])
        final_insight = f"[SYSTEM OVERRIDE] 판독 불가. 파워 스코어 1위 종목 강제 지정.\n{insight}"
        t_print(f"\n   ⚠️ [시스템 오버라이드] 1위 종목 [{final_target}] 강제 채택.\n")

    t_print("="*75)

    cand_df_final = df_dict[final_target]
    atr = calculate_true_atr(cand_df_final['High'], cand_df_final['Low'], cand_df_final['Close'], period=14)

    try:
        intraday_1m = yf.Ticker(final_target).history(period="1d", interval="1m", prepost=True)
        yf_live_price = float(intraday_1m['Close'].iloc[-1]) if not intraday_1m.empty else 0.0
    except: yf_live_price = 0.0

    yesterday_close = float(cand_df_final['Close'].iloc[-2]) if len(cand_df_final) > 1 else float(cand_df_final['Close'].iloc[-1])
    
    vwap = pm_vwap_val if pm_vwap_val > 0 else yf_live_price
    
    if yf_live_price and yf_live_price != yesterday_close: entry_price, price_src = yf_live_price, "yfinance 실시간"
    else: entry_price, price_src = yesterday_close, "전일 종가 (API 지연)"
        
    cap_scale = 1.0
    if market_cap_val > 100_000_000_000: cap_scale = 0.5    
    elif market_cap_val > 20_000_000_000: cap_scale = 0.7   

    if vwap == 0.0: vwap = entry_price
    
    entry_2_val = (entry_price - (atr * 0.5 * cap_scale)) if is_pre_market else (entry_price - (atr * 0.3 * cap_scale) if abs(vwap - entry_price) / entry_price < 0.002 else vwap)
    avg_entry = (entry_price + entry_2_val) / 2.0
    
    risk_multiplier = 1.2 if is_doomsday else 1.0

    sl_distance = max(atr * risk_multiplier * cap_scale, avg_entry * 0.01)
    base_hard_stop = avg_entry - sl_distance

    gap_discount = 1.0
    if gap_pct_val > 0:
        gap_discount = max(0.5, 1.0 - (gap_pct_val / 10.0))

    reward_unit = max(atr * 0.8 * cap_scale * gap_discount, avg_entry * 0.008)
    raw_tp1 = avg_entry + reward_unit
    
    theoretical_ceiling = yesterday_close + (atr * 1.5)  
    tp1_trigger = min(raw_tp1, theoretical_ceiling * 0.998) 
    
    if pm_high_val > avg_entry and tp1_trigger > pm_high_val:
        tp1_trigger = max(pm_high_val * 0.998, avg_entry * 1.005) 

    tp2_raw = avg_entry + (reward_unit * 3.0)
    tp2_trigger = min(tp2_raw, theoretical_ceiling * 1.01) 
    if tp2_trigger <= tp1_trigger: tp2_trigger = tp1_trigger + (avg_entry * 0.005)

    def get_offset(price): return max(0.10, price * 0.002)

    tp1_limit = tp1_trigger - get_offset(tp1_trigger)
    tp2_limit = tp2_trigger - get_offset(tp2_trigger)
    sl1_trigger, sl2_trigger = base_hard_stop, base_hard_stop - 0.10
    
    buy2_target_price = entry_2_val

    profit_per_share, risk_per_share = tp1_trigger - avg_entry, avg_entry - base_hard_stop
    ideal_total_qty = max(1, int(TARGET_PROFIT_USD // profit_per_share) + 1) * 2
    qty = min(ideal_total_qty, max(2, int(MAX_RISK_USD // risk_per_share)) if risk_per_share > 0 else ideal_total_qty, max(2, int(SLOT_CAPITAL // avg_entry)))
    if qty % 2 != 0: qty -= 1 
    if qty < 2: qty = 2
    half_qty, expected_profit_at_t1, max_total_loss = qty // 2, profit_per_share * (qty // 2), risk_per_share * qty

    entry_1_desc = "**[1차 즉시 매수]** 지금 일반주문으로 ➔ 시장가 긁으십시오."
    shot_title = "세컨드 샷 (2순위)" if is_second_bullet else "오늘의 1순위 폭파 타겟"

    t_print(f"\n**[🚀 OVERDRIVE COMMAND READY]**")
    if is_doomsday: t_print(f"🩸 **[DOOMSDAY INVERSE MODE]** 폭락장 방어 및 숏 스퀴즈 헌팅 모드!")
    t_print(f"### 🎯 [{shot_title}] {final_target}")
    
    discount_texts = []
    if gap_discount < 1.0: discount_texts.append(f"Gap 삭감 {gap_discount:.2f}x")
    if cap_scale < 1.0: discount_texts.append(f"시총 압축 {cap_scale:.2f}x")
    discount_str = f" (스케일링: {' / '.join(discount_texts)})" if discount_texts else ""
    gap_str = f"+{gap_pct_val:.2f}%" if gap_pct_val > 0 else f"{gap_pct_val:.2f}%"
    
    t_print(f"🔥 **[펀더멘털 스탯]:** Power Score: **{final_power_score:.2f}** | RS: {final_rs:.4f} | 예상 RVOL: {final_vol_spike:.2f}x")
    t_print(f"   ➔ 당일 갭: {gap_str}{discount_str}")
    t_print(f"* **단가 출처:** {price_src}")
    t_print(f"* **예상 평균 진입 단가(평단가):** **${avg_entry:.2f}**")
    t_print(f"* **ATR(진폭):** ${atr:.2f} / **VWAP:** ${vwap:.2f} / 🛡️ **프리장 최고점(저항선):** ${pm_high_val:.2f}")
    t_print(f"* 🔮 **당일 예측 천장 (Ceiling):** **${theoretical_ceiling:.2f}** (천장 캡핑 적용됨)")
    t_print(f"* 🛡️ **파산 방지 안전핀:** 전량 손절 시 최대 리스크 - **${max_total_loss:,.0f} (자본 1.5% 한도)**\n")
    
    t_print(f"> 🧠 **[APEX 시각 지능 심사평]:**\n{final_insight}\n")

    t_print("### 🤖 [OVERDRIVE 조준표 100% 카피 UI: 시가 갭하락 방어 셋업]")
    t_print(f"| 앱 메뉴 | 🔔 조건 (감시가) | 🛒 주문 세팅 (수량 / 지정가·시장가) | 비고 |")
    t_print(f"| :--- | :--- | :--- | :--- |")
    t_print(f"| **🔵 일반 구매** | 즉시 실행 | **{half_qty}주** · **${entry_price:.2f} 부근** | {entry_1_desc} |")
    t_print(f"| **🔵 일반 구매** | **(조건 설정 없음)** | **{half_qty}주** · 지정가 **${buy2_target_price:.2f}** | [2차 매복] ⭐️ 갭하락 대비 '일반주문' 탭에서 지정가로 미리 깔아둠! |")
        
    t_print(f"| **🔴 조건 판매** | **${sl1_trigger:.2f}** 이하일 때 | ➔ **{half_qty}주** · **시장가** | [1차 방패] 50% 분할 손절망 |")
    t_print(f"| **🔴 조건 판매** | **${sl2_trigger:.2f}** 이하일 때 | ➔ **{half_qty}주** · **시장가** | [2차 방패] 50% (에러 방지) |")
    t_print(f"| **🟢 조건 판매** | **${tp1_trigger:.2f}** 이상일 때 | ➔ **{half_qty}주** · 지정가 **${tp1_limit:.2f}** | [1차 익절] 체결 보장 |")
    t_print(f"| **🚀 조건 판매** | **${tp2_trigger:.2f}** 이상일 때 | ➔ **{half_qty}주** · 지정가 **${tp2_limit:.2f}** | [2차 런너] 천장 개방 |")

    break_even_stop_limit = avg_entry - get_offset(avg_entry)
    t_print("\n" + "="*80)
    t_print(" ⏰ [MOC 심판의 시간: 장 마감 10분 전 수동 액션 프로토콜]")
    t_print("--------------------------------------------------------------------------------")
    t_print(f" ▶️ **현재가 확인 절대 기준점 (내 평단가): ${avg_entry:.2f}**")
    t_print(f" 💀 **[시나리오 A: 손실 중] 현재가 < ${avg_entry:.2f}**")
    t_print(f"    ➔ 펌핑 실패! 조건주문 싹 다 취소하고, 남은 수량 전량 **'시장가 매도' (타임 컷)**")
    t_print(f" 🚀 **[시나리오 B: 수익 중] 현재가 >= ${avg_entry:.2f}**")
    t_print(f"    ➔ 무위험 스윙! 기존 🔴 조건 판매(손절망) 2개 취소 후, 아래 1개로 재세팅.")
    t_print(f"    ➔ 새로운 🔴 조건 판매: 감시가 **${avg_entry:.2f}** 이하 / 지정가 **${break_even_stop_limit:.2f}**")
    t_print("="*80)

    t_print("\n---------------------------------------------------------------------")
    t_print("   🧠 [CHIEF MINDSET OFFICER: 수면 매매 가이드]")
    t_print("---------------------------------------------------------------------")
    t_print("\n### **[" + final_target + "] 진입 전 최종 브리핑**\n---\n" + ask_gemini_mindset_coach(final_target, expected_profit_at_t1, max_total_loss, qty, avg_entry, is_second_bullet, is_doomsday))
    
    t_print("\n========================= [OVERDRIVE CODE FREEZE] =========================")
    
    # 🚨 [가장 중요] 쌓인 터미널 로그를 텔레그램으로 한방에 전송
    flush_telegram()

# ==========================================
# 🌐 [원격 자율 사냥 트리거 (API)]
# ==========================================
@app.route('/hunt', methods=['GET'])
def trigger_hunt_manual():
    """스마트폰 브라우저 접속 시 자율 스캔 스레드 기동"""
    threading.Thread(target=overdrive_apex_execution).start()
    return "🦅 OVERDRIVE AUTONOMOUS HUNTER INITIATED. CHECK TELEGRAM IN 1-2 MIN.", 200

@app.route('/webhook', methods=['POST'])
def trigger_hunt_auto():
    """트레이딩뷰가 특정 시간에 때리면 자동 사냥 시작"""
    threading.Thread(target=overdrive_apex_execution).start()
    return jsonify({"status": "success", "message": "Autohunt Initiated"}), 200

@app.route('/', methods=['GET'])
def index():
    return "👑 OVERDRIVE NEXUS IS ONLINE.", 200

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
