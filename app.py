import streamlit as st
import pandas as pd
import gspread
from gspread_dataframe import get_as_dataframe
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime

# ---------------------------------------------------------
# 1️⃣ 페이지 기본 설정
# ---------------------------------------------------------
st.set_page_config(page_title="VOC Dashboard", layout="wide")

st.markdown("""
    <style>
        body {
            background-color: #f5f6fa;
        }
        .main-card {
            background-color: #ffffff;
            border-radius: 15px;
            padding: 25px;
            margin-bottom: 20px;
            box-shadow: 0px 2px 6px rgba(0,0,0,0.1);
        }
        h2 {
            font-weight: 700;
            color: #2f3640;
            margin-bottom: 20px;
        }
        .metric-box {
            background-color: #f8f9fb;
            padding: 15px;
            border-radius: 12px;
            text-align: center;
            margin-bottom: 10px;
        }
        .metric-value {
            font-size: 26px;
            font-weight: 700;
            color: #273c75;
        }
        .metric-label {
            font-size: 14px;
            color: #718093;
        }
    </style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------
# 2️⃣ Google Sheets 연결
# ---------------------------------------------------------
SERVICE_ACCOUNT_FILE = 'service_account.json'
SPREADSHEET_NAME = 'VOC 대시보드 데이터'
SHEET_NAME = '25-10'  # ← 현재 월 탭

try:
    scope = ['https://spreadsheets.google.com/feeds',
             'https://www.googleapis.com/auth/drive']
    creds = ServiceAccountCredentials.from_json_keyfile_name(SERVICE_ACCOUNT_FILE, scope)
    gc = gspread.authorize(creds)
    spreadsheet = gc.open(SPREADSHEET_NAME)
    worksheet = spreadsheet.worksheet(SHEET_NAME)
    df = get_as_dataframe(worksheet)
    df = df.dropna(how='all')
    st.session_state['df'] = df
    print("✅ Google Sheets 로드 성공.")
except Exception as e:
    st.error(f"❌ Google Sheets 연결 실패: {e}")
    st.stop()

# ---------------------------------------------------------
# 3️⃣ 데이터 전처리
# ---------------------------------------------------------
df = st.session_state['df']
if '접수일' in df.columns:
    df['날짜'] = pd.to_datetime(df['접수일'], errors='coerce').dt.strftime('%y%m%d')
else:
    df['날짜'] = ''

today = datetime.now()
yesterday = today.replace(day=today.day - 1)
yesterday_str = yesterday.strftime('%y%m%d')

df_yesterday = df[df['날짜'] == yesterday_str]

# ---------------------------------------------------------
# 4️⃣ 🚀 핵심 지표 요약 (여백 제거 버전)
# ---------------------------------------------------------
with st.container():
    st.markdown('<div class="main-card">', unsafe_allow_html=True)
    st.markdown('<h2>🚀 핵심 지표 요약</h2>', unsafe_allow_html=True)

    col1, col2, col3 = st.columns(3)
    with col1:
        st.markdown('<div class="metric-box">', unsafe_allow_html=True)
        st.markdown(f'<div class="metric-value">{len(df_yesterday):,}</div>', unsafe_allow_html=True)
        st.markdown('<div class="metric-label">전일 VOC 건수</div>', unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)

    with col2:
        completed = df_yesterday[df_yesterday['처리일'].notna()]
        st.markdown('<div class="metric-box">', unsafe_allow_html=True)
        st.markdown(f'<div class="metric-value">{len(completed):,}</div>', unsafe_allow_html=True)
        st.markdown('<div class="metric-label">처리 완료</div>', unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)

    with col3:
        pending = len(df_yesterday) - len(completed)
        st.markdown('<div class="metric-box">', unsafe_allow_html=True)
        st.markdown(f'<div class="metric-value">{pending:,}</div>', unsafe_allow_html=True)
        st.markdown('<div class="metric-label">미처리</div>', unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)

    # -----------------------------------------------------
    # ✅ 전일 VOC 컨디션 분석 - 같은 카드 내부에 포함
    # -----------------------------------------------------
    st.markdown("<h3>📊 전일 VOC 컨디션 분석</h3>", unsafe_allow_html=True)

    if not df_yesterday.empty:
        issue_counts = df_yesterday['접수 카테고리'].value_counts().head(5)
        st.bar_chart(issue_counts)
    else:
        st.info("전일 VOC 데이터가 없습니다.")

    # ✅ 카드 닫기
    st.markdown('</div>', unsafe_allow_html=True)

# ---------------------------------------------------------
# 5️⃣ 🔍 게임별 상세 이슈 분석
# ---------------------------------------------------------
with st.container():
    st.markdown('<div class="main-card">', unsafe_allow_html=True)
    st.markdown("<h2>🔍 게임별 상세 이슈 분석</h2>", unsafe_allow_html=True)

    if '게임명' in df.columns:
        selected_game = st.selectbox("게임 선택", options=df['게임명'].dropna().unique())
        game_data = df[df['게임명'] == selected_game]

        st.dataframe(game_data[['접수번호', '접수일', '상담제목', '문의내용', '답변내용']])
    else:
        st.warning("게임명 컬럼이 없습니다.")

    st.markdown('</div>', unsafe_allow_html=True)

# ---------------------------------------------------------
# 6️⃣ 📈 VOC 트렌드 요약
# ---------------------------------------------------------
with st.container():
    st.markdown('<div class="main-card">', unsafe_allow_html=True)
    st.markdown("<h2>📈 VOC 트렌드 요약</h2>", unsafe_allow_html=True)

    if '날짜' in df.columns:
        trend = df.groupby('날짜').size()
        st.line_chart(trend)
    else:
        st.info("날짜 데이터가 없습니다.")

    st.markdown('</div>', unsafe_allow_html=True)
