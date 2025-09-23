# -*- coding: utf-8 -*-
import streamlit as st
import pandas as pd
import plotly.express as px
from datetime import datetime, timedelta
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from wordcloud import WordCloud
import matplotlib.pyplot as plt
import re

# --- 1. 기본 설정 ---
# Google Sheets 및 페이지 설정
SPREADSHEET_ID = "1rgR21yUBtKSJKE4KYdBvoZzDU-0jZ_wLb6Tqc_zS7RM"
SERVICE_ACCOUNT_FILE = "service_account.json"

st.set_page_config(page_title="피망 웹보드 VOC 대시보드", page_icon="📊", layout="wide")

# L2 태그를 L1 대분류로 매핑하기 위한 딕셔너리
L2_TO_L1_MAPPING = {
    '로그인/인증': '계정', '정보 관리': '계정',
    '기술 오류': '시스템/환경',
    '결제 오류/미지급': '재화/결제', '환불/청약철회': '재화/결제', '재화 소실/오류': '재화/결제',
    '클래스/구독 상품': '재화/결제', '재화 정책/한도': '재화/결제',
    '밸런스/불만 (패몰림)': '게임 플레이', '콘텐츠 오류/문의': '게임 플레이', '토너먼트/대회': '게임 플레이',
    '점령전/거점전': '게임 플레이', '랭킹페스타': '게임 플레이', '연승챌린지': '게임 플레이', '패밀리게임': '게임 플레이',
    '광고/무료충전소': '이벤트/혜택', '이벤트': '이벤트/혜택',
    '비매너/욕설 신고': '불량 이용자', '제재 문의': '불량 이용자',
    '콘텐츠/시스템 건의': '정책/건의 (VOC)', '운영/정책 건의': '정책/건의 (VOC)',
    '단순 문의/미분류': '기타'
}

# --- 2. 데이터 처리 함수 ---

def classify_game(category):
    """'MOB섯다', 'PC뉴맞고' 등 다양한 형식을 표준 게임 이름으로 통합하는 함수"""
    if pd.isna(category):
        return "기타"
    
    category = str(category).lower()
    
    if "뉴맞고" in category or "newmatgo" in category: return "뉴맞고"
    if "섯다" in category or "sutda" in category: return "섯다"
    if "포커" in category or "poker" in category: return "포커"
    if "쇼다운홀덤" in category or "showdown" in category: return "쇼다운홀덤"
    if "뉴베가스" in category or "newvegas" in category: return "뉴베가스"
    return "기타"

@st.cache_data(ttl=600) # 10분마다 데이터 새로고침
def load_data():
    """Google Sheets에서 모든 VOC 데이터를 불러오고 전처리하는 함수"""
    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds = ServiceAccountCredentials.from_json_keyfile_name(SERVICE_ACCOUNT_FILE, scope)
        gc = gspread.authorize(creds)
        
        spreadsheet = gc.open_by_key(SPREADSHEET_ID)
        worksheets = spreadsheet.worksheets()
        all_data = []
        
        for worksheet in worksheets:
            if worksheet.title.lower() not in ["sheet1", "template", "mapping"]:
                try:
                    data = worksheet.get_all_records()
                    if data:
                        for row in data: row["날짜"] = worksheet.title
                        all_data.extend(data)
                except Exception: continue
        
        if not all_data: return pd.DataFrame()
        df = pd.DataFrame(all_data)
        
        required_cols = ["접수 카테고리", "상담제목", "문의내용", "taglist"]
        if not all(col in df.columns for col in required_cols):
            st.error(f"필수 컬럼({required_cols})이 Google Sheets에 없습니다.")
            return pd.DataFrame()

        df.rename(columns={'taglist': 'L2 태그'}, inplace=True)
        df["게임"] = df["접수 카테고리"].apply(classify_game)
        df["날짜_dt"] = pd.to_datetime(df["날짜"], format='%y%m%d', errors='coerce')
        df = df.dropna(subset=["날짜_dt"])
        df['L1 태그'] = df['L2 태그'].map(L2_TO_L1_MAPPING).fillna('기타')
        df['주차'] = df['날짜_dt'].dt.to_period('W-MON')
        return df

    except Exception as e:
        st.error(f"Google Sheets 연결 또는 데이터 처리 중 오류 발생: {e}")
        return pd.DataFrame()

def clean_text_for_wordcloud(text):
    if not isinstance(text, str): return ""
    text = re.sub(r'회원번호\s*:\s*\d+|회원분류\s*:\s*\w+|루팅여부\s*:\s*\w+|OS version\s*:.*|app version\s*:.*|휴대폰기기정보\s*:.*', '', text)
    text = re.sub(r'[^ㄱ-ㅎㅏ-ㅣ가-힣\s]', '', text)
    return text.strip()

def classify_sentiment(text):
    if not isinstance(text, str): return "중립"
    positive_keywords = ["감사합니다", "좋아요", "도움이 되었습니다", "해결", "고맙습니다"]
    negative_keywords = ["짜증", "오류", "환불", "안돼요", "쓰레기", "조작", "불만", "문제", "패몰림", "오링"]
    text_lower = text.lower()
    if any(keyword in text_lower for keyword in negative_keywords): return "부정"
    if any(keyword in text_lower for keyword in positive_keywords): return "긍정"
    return "중립"

def generate_wordcloud(text_data):
    cleaned_texts = [clean_text_for_wordcloud(text) for text in text_data]
    text = ' '.join(cleaned_texts)
    
    if not text.strip():
        st.info("워드클라우드를 생성할 키워드가 충분하지 않습니다.")
        return

    font_path = 'c:/Windows/Fonts/malgun.ttf'
    korean_stopwords = ['문의', '게임', '피망', '고객', '내용', '확인', '답변', '부탁', '처리', '관련', '안녕하세요']
    
    try:
        wordcloud = WordCloud(
            font_path=font_path, width=800, height=400, background_color='white',
            stopwords=set(korean_stopwords)
        ).generate(text)
        
        fig, ax = plt.subplots(figsize=(5, 2.5))
        ax.imshow(wordcloud, interpolation='bilinear')
        ax.axis('off')
        st.pyplot(fig)
    except FileNotFoundError:
        st.warning("워드클라우드 생성을 위한 한글 폰트를 찾을 수 없습니다. (malgun.ttf)")
    except Exception as e:
        st.error(f"워드클라우드 생성 중 오류 발생: {e}")

# --- 3. UI 구성 ---
st.title("📊 피망 웹보드 VOC 대시보드")
st.markdown("---")

voc_data = load_data()

if voc_data.empty:
    st.warning("표시할 데이터가 없습니다. Google Sheets를 확인해주세요.")
else:
    with st.sidebar:
        st.title("🎮 VOC 대시보드 필터")
        game_list = ["전체", "뉴맞고", "섯다", "포커", "쇼다운홀덤", "뉴베가스"]
        selected_game = st.selectbox("게임을 선택하세요:", game_list)
        
        st.markdown("---")
        st.markdown("### 📅 기간 선택")
        def set_date_range(days):
            max_date = voc_data['날짜_dt'].max().date()
            st.session_state.date_range = (max_date - timedelta(days=days-1), max_date)
        col1, col2 = st.columns(2)
        with col1: st.button("최근 7일", on_click=set_date_range, args=(7,), use_container_width=True)
        with col2: st.button("최근 30일", on_click=set_date_range, args=(30,), use_container_width=True)
        if 'date_range' not in st.session_state: set_date_range(7)
        date_range = st.date_input("조회 기간:", key='date_range', min_value=voc_data['날짜_dt'].min().date(), max_value=voc_data['날짜_dt'].max().date())

    if selected_game != "전체":
        filtered_data = voc_data[voc_data["게임"] == selected_game].copy()
    else:
        filtered_data = voc_data.copy()

    if len(date_range) == 2:
        start_date = pd.to_datetime(date_range[0])
        end_date = pd.to_datetime(date_range[1])
        filtered_data = filtered_data[(filtered_data["날짜_dt"] >= start_date) & (filtered_data["날짜_dt"] <= end_date)]

    if filtered_data.empty:
        st.warning(f"선택하신 기간과 게임에 해당하는 VOC 데이터가 없습니다.")
    else:
        st.header("🚀 핵심 지표 요약")
        current_period_start = pd.to_datetime(date_range[0])
        current_period_end = pd.to_datetime(date_range[1])
        period_days = (current_period_end - current_period_start).days + 1
        previous_period_start = current_period_start - timedelta(days=period_days)
        previous_period_end = current_period_start - timedelta(days=1)
        previous_data_base = voc_data[(voc_data["날짜_dt"] >= previous_period_start) & (voc_data["날짜_dt"] <= previous_period_end)]
        if selected_game != "전체":
            previous_data = previous_data_base[previous_data_base["게임"] == selected_game]
        else:
            previous_data = previous_data_base
        current_count = len(filtered_data)
        previous_count = len(previous_data)
        change_percent = ((current_count - previous_count) / previous_count * 100) if previous_count > 0 else 0
        top3_categories = filtered_data['L2 태그'].value_counts().nlargest(3)

        col1, col2, col3 = st.columns(3)
        col1.metric("총 VOC 건수", f"{current_count} 건")
        col2.metric("이전 기간 대비", f"{change_percent:.1f} %", f"{current_count - previous_count} 건")
        with col3:
            st.markdown("**주요 카테고리 TOP 3**")
            for i, (cat, count) in enumerate(top3_categories.items()):
                st.markdown(f"**{i+1}.** {cat} ({count}건)")

        st.markdown("---")
        
        st.header("📅 월별 VOC 발생 추이")
        today = datetime.now()
        first_day_of_month = today.replace(day=1)
        # Get the number of days in the current month
        next_month = first_day_of_month.replace(month=first_day_of_month.month % 12 + 1, year=first_day_of_month.year + first_day_of_month.month // 12)
        last_day_of_month = next_month - timedelta(days=1)
        
        all_days_in_month = pd.date_range(start=first_day_of_month.date(), end=last_day_of_month.date(), freq='D')
        month_df = pd.DataFrame(all_days_in_month, columns=['날짜_dt'])
        daily_counts = filtered_data.groupby(filtered_data['날짜_dt'].dt.date).size().reset_index(name="건수")
        daily_counts['날짜_dt'] = pd.to_datetime(daily_counts['날짜_dt'])
        merged_daily_data = pd.merge(month_df, daily_counts, on='날짜_dt', how='left').fillna(0)
        
        fig_daily_trend = px.line(
            merged_daily_data, x='날짜_dt', y='건수',
            title=f"<b>{today.strftime('%Y년 %m월')} 일자별 VOC 추이</b>",
            labels={'날짜_dt': '날짜', '건수': 'VOC 건수'}, markers=True
        )
        fig_daily_trend.update_layout(xaxis_title="", yaxis_title="건수")
        st.plotly_chart(fig_daily_trend, use_container_width=True)
        st.markdown("---")

        tab_main, tab_search = st.tabs(["📊 카테고리 분석", "🔍 키워드 검색"])
        with tab_main:
            st.header("📌 VOC 카테고리별 현황")
            l2_counts = filtered_data['L2 태그'].value_counts().nlargest(10).sort_values(ascending=True)
            fig_l2 = px.bar(l2_counts, x=l2_counts.values, y=l2_counts.index, orientation='h', title="<b>태그별 현황 TOP 10</b>", labels={'x': '건수', 'y': '태그'})
            st.plotly_chart(fig_l2, use_container_width=True)
            st.subheader("📑 VOC 원본 데이터")
            all_categories = sorted(filtered_data['L2 태그'].unique())
            selected_categories = st.multiselect("확인하고 싶은 카테고리를 선택하세요:", options=all_categories, default=top3_categories.index.tolist())
            if selected_categories:
                display_data = filtered_data[filtered_data['L2 태그'].isin(selected_categories)]
                st.dataframe(display_data[["날짜", "게임", "L2 태그", "상담제목", "문의내용"]], use_container_width=True, height=500)

        with tab_search:
            st.header("🔍 키워드 검색")
            search_keyword = st.text_input("분석하고 싶은 키워드를 입력하세요:", placeholder="예: 환불, 튕김, 업데이트...")
            if search_keyword:
                search_results = filtered_data[filtered_data["상담제목"].str.contains(search_keyword, na=False, case=False) | filtered_data["문의내용"].str.contains(search_keyword, na=False, case=False)].copy()
                if not search_results.empty:
                    st.success(f"✅ \"{search_keyword}\" 키워드가 포함된 VOC: **{len(search_results)}**건")
                    search_results['감성'] = search_results['문의내용'].apply(classify_sentiment)
                    sentiment_counts = search_results['감성'].value_counts()
                    st.subheader("감성 분석 결과")
                    sentiment_cols = st.columns(3)
                    sentiment_cols[0].metric("긍정 😊", f"{sentiment_counts.get('긍정', 0)} 건")
                    sentiment_cols[1].metric("부정 😠", f"{sentiment_counts.get('부정', 0)} 건")
                    sentiment_cols[2].metric("중립 😐", f"{sentiment_counts.get('중립', 0)} 건")
                    
                    st.subheader("관련 VOC 목록")
                    st.dataframe(search_results[["날짜", "L2 태그", "상담제목", "문의내용", "감성"]], use_container_width=True, height=400)
                    
                    st.subheader("연관 키워드 워드클라우드")
                    generate_wordcloud(search_results["문의내용"])
                else:
                    st.warning(f"⚠️ \"{search_keyword}\" 키워드가 포함된 VOC가 없습니다.")

