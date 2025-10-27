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
# [수정] SPREADSHEET_ID를 사용하는 단일 파일 방식으로 복구합니다.
SPREADSHEET_ID = "1rgR21yUBtKSJKE4KYdBvoZzDU-0jZ_wLb6Tqc_zS7RM"
SERVICE_ACCOUNT_FILE = 'service_account.json'

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
    """'접수 카테고리'에서 표준 게임 이름을 추출하는 함수"""
    if pd.isna(category): return "기타"
    category = str(category).lower()
    if "뉴맞고" in category or "newmatgo" in category: return "뉴맞고"
    if "섯다" in category or "sutda" in category: return "섯다"
    if "포커" in category or "poker" in category: return "포커"
    if "쇼다운홀덤" in category or "showdown" in category: return "쇼다운홀덤"
    if "뉴베가스" in category or "newvegas" in category: return "뉴베가스"
    return "기타"

def classify_platform(category):
    """'접수 카테고리'에서 플랫폼 정보를 추출하는 함수"""
    if pd.isna(category): return "기타"
    category = str(category).lower()
    if "for kakao" in category: return "for kakao"
    if "mob" in category or "모바일" in category: return "MOB"
    if "pc" in category: return "PC"
    return "기타"

def extract_gsn_usn(row):
    """'문의내용' 또는 '고객정보'에서 GSN(모바일) 또는 USN(PC)을 추출하는 함수"""
    platform = row.get('플랫폼', '')
    
    if platform in ['MOB', 'for kakao']:
        inquiry_content = str(row.get('문의내용', ''))
        gsn_match = re.search(r'회원번호\s*:\s*(\d+)', inquiry_content)
        if gsn_match:
            return gsn_match.group(1)
            
    if platform == 'PC':
        customer_info = str(row.get('고객정보', ''))
        usn_match = re.search(r'\d+', customer_info)
        if usn_match:
            return usn_match.group(0)
            
    return ""

def extract_device_info(row):
    """'문의내용'에서 기기정보를 추출하거나 플랫폼에 따라 PC로 지정하는 함수"""
    inquiry_content = str(row.get('문의내용', ''))
    device_match = re.search(r'휴대폰기기정보\s*:\s*(\S+)', inquiry_content)
    if device_match:
        return device_match.group(1)
    
    platform = row.get('플랫폼', '')
    if platform == 'PC':
        return 'PC'
        
    return ""

def truncate_inquiry_content(text):
    """문의 내용에서 정형화된 템플릿 부분을 제거하는 함수"""
    if isinstance(text, str):
        return text.split("회원번호 :")[0].strip()
    return ""

@st.cache_data(ttl=600)
def load_data():
    """[수정] Google Sheets에서 '단일 파일'의 모든 시트를 읽어옴"""
    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        
        # --- [수정] Secrets 대신 로컬 파일 인증으로 복귀 ---
        try:
            creds = ServiceAccountCredentials.from_json_keyfile_name(SERVICE_ACCOUNT_FILE, scope)
            gc = gspread.authorize(creds)
            print("✅ Google Sheets API 인증 성공.")
        except Exception as e:
            st.error(f"❌ Google Sheets 인증 정보 로드 실패: {e}")
            st.error(f"`{SERVICE_ACCOUNT_FILE}` 파일이 `app.py`와 같은 폴더에 있는지 확인하세요.")
            return pd.DataFrame()
        # --- [수정 완료] ---
        
        all_data_frames = []
        print(f"Google Sheets 파일({SPREADSHEET_ID})을 엽니다...")
        
        spreadsheet = gc.open_by_key(SPREADSHEET_ID)
        
        print(f"--- '{spreadsheet.title}' 파일 처리 중 ---")
        worksheets = spreadsheet.worksheets()
        
        for worksheet in worksheets:
            if worksheet.title.lower() not in ["sheet1", "template", "mapping"]:
                try:
                    print(f"  '{worksheet.title}' 시트 읽는 중...")
                    data_records = worksheet.get_all_records()
                    if data_records:
                        df_sheet = pd.DataFrame(data_records)
                        df_sheet["날짜"] = worksheet.title
                        all_data_frames.append(df_sheet)
                except Exception as e:
                    st.warning(f"'{worksheet.title}' 시트 처리 중 오류: {e}")
        
        if not all_data_frames:
            st.error("데이터가 있는 시트를 찾을 수 없습니다.")
            return pd.DataFrame()
        
        print("모든 데이터를 하나로 병합합니다...")
        df = pd.concat(all_data_frames, ignore_index=True)
        
        required_cols = ["접수 카테고리", "상담제목", "문의내용", "taglist"]
        if not all(col in df.columns for col in required_cols):
            st.error(f"필수 컬럼({required_cols})이 Google Sheets에 없습니다.")
            return pd.DataFrame()

        df.rename(columns={'taglist': 'L2 태그'}, inplace=True)
        df["게임"] = df["접수 카테고리"].apply(classify_game)
        df["플랫폼"] = df["접수 카테고리"].apply(classify_platform)
        df["날짜_dt"] = pd.to_datetime(df["날짜"], format='%y%m%d', errors='coerce')
        df = df.dropna(subset=["날짜_dt"])
        df['L1 태그'] = df['L2 태그'].map(L2_TO_L1_MAPPING).fillna('기타')
        
        df['GSN(USN)'] = df.apply(extract_gsn_usn, axis=1)
        df['기기정보'] = df.apply(extract_device_info, axis=1)
        df['문의내용_요약'] = df['문의내용'].apply(truncate_inquiry_content)
        df['검색용_문의내용'] = df['문의내용_요약']
        
        print(f"총 {len(df)}건의 데이터 로드 완료.")
        return df

    except Exception as e:
        st.error(f"Google Sheets 연결 또는 데이터 처리 중 오류 발생: {e}")
        return pd.DataFrame()

def clean_text_for_wordcloud(text):
    if not isinstance(text, str): return ""
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
            font_path=font_path, width=800, height=400, background_color='white', stopwords=set(korean_stopwords)
        ).generate(text)
        fig, ax = plt.subplots(figsize=(5, 2.5))
        ax.imshow(wordcloud, interpolation='bilinear')
        ax.axis('off')
        st.pyplot(fig)
    except FileNotFoundError:
        st.warning("워드클라우드 생성을 위한 한글 폰트를 찾을 수 없습니다. (malgun.ttf)")
    except Exception as e: st.error(f"워드클라우드 생성 중 오류 발생: {e}")

def mask_phone_number(text):
    if not isinstance(text, str): return text
    masked_text = re.sub(r'(010[-.\s]?)\d{3,4}([-.\s]?)\d{4}', r'\1****\2****', text)
    return masked_text

# --- 3. UI 구성 ---
st.title("📊 피망 웹보드 VOC 대시보드")
st.markdown("---")

voc_data = load_data()

if voc_data.empty:
    st.warning("표시할 데이터가 없습니다. Google Sheets를 확인해주세요.")
else:
    with st.sidebar:
        st.title("🎮 VOC 대시보드 필터")
        
        st.markdown("### 🕹️ 게임 및 플랫폼 선택")
        game_filters = {
            "뉴맞고": ["뉴맞고 (전체)", "뉴맞고 MOB", "뉴맞고 PC", "뉴맞고 for kakao"],
            "섯다": ["섯다 (전체)", "섯다 MOB", "섯다 PC", "섯다 for kakao"],
            "포커": ["포커 (전체)", "포커 MOB", "포커 PC", "포커 for kakao"],
            "쇼다운홀덤": ["쇼다운홀덤 (전체)", "쇼다운홀덤 MOB", "쇼다운홀덤 PC"],
            "뉴베가스": ["뉴베가스 (전체)", "뉴베가스 MOB", "뉴베가스 PC"],
            "기타": ["기타"]
        }
        
        all_options_with_groups = [opt for sublist in game_filters.values() for opt in sublist]
        all_child_options = [opt for game, opts in game_filters.items() for opt in (opts[1:] if "(전체)" in opts[0] else opts)]
        
        def master_checkbox_callback():
            is_all_selected = st.session_state.get('select_all', False)
            for option in all_options_with_groups:
                st.session_state[option] = is_all_selected

        def group_checkbox_callback(game_key):
            is_group_selected = st.session_state.get(f"{game_key} (전체)", False)
            for option in game_filters[game_key][1:]:
                st.session_state[option] = is_group_selected
            update_master_checkbox()

        def child_checkbox_callback(game_key):
            if len(game_filters[game_key]) > 1:
                all_children_selected = all(st.session_state.get(opt, False) for opt in game_filters[game_key][1:])
                st.session_state[f"{game_key} (전체)"] = all_children_selected
            update_master_checkbox()

        def update_master_checkbox():
            all_groups_selected = all(st.session_state.get(f"{game} (전체)", False) for game, opts in game_filters.items() if len(opts) > 1 and "(전체)" in opts[0])
            all_single_games_selected = all(st.session_state.get(opts[0], False) for game, opts in game_filters.items() if len(opts) == 1)
            st.session_state.select_all = all_groups_selected and all_single_games_selected
        
        if 'filters_initialized' not in st.session_state:
            st.session_state.filters_initialized = True
            st.session_state.select_all = True
            for option in all_options_with_groups:
                st.session_state[option] = True
        
        st.checkbox("전체", key='select_all', on_change=master_checkbox_callback)

        for game, options in game_filters.items():
            with st.expander(game, expanded=True):
                if len(options) > 1 and "(전체)" in options[0]:
                    st.checkbox(options[0], key=options[0], on_change=group_checkbox_callback, args=(game,))
                    for option in options[1:]:
                        st.checkbox(option, key=option, on_change=child_checkbox_callback, args=(game,))
                else:
                    st.checkbox(options[0], key=options[0], on_change=update_master_checkbox)

        selected_options = [option for option in all_child_options if st.session_state.get(option, False)]
        
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

    if not selected_options:
        filtered_data = pd.DataFrame()
    else:
        conditions = []
        for option in selected_options:
            if " for kakao" in option:
                game_name = option.replace(" for kakao", "")
                platform_name = "for kakao"
                conditions.append((voc_data['게임'] == game_name) & (voc_data['플랫폼'] == platform_name))
            elif len(option.split(" ")) > 1:
                parts = option.split(" ", 1)
                game_name = parts[0]
                platform_name = parts[1]
                conditions.append((voc_data['게임'] == game_name) & (voc_data['플랫폼'] == platform_name))
            else:
                conditions.append(voc_data['게임'] == option)
        if conditions:
            final_condition = pd.concat(conditions, axis=1).any(axis=1)
            filtered_data = voc_data[final_condition].copy()
        else:
            filtered_data = pd.DataFrame()
            
    if not filtered_data.empty and len(date_range) == 2:
        start_date = pd.to_datetime(date_range[0])
        end_date = pd.to_datetime(date_range[1])
        filtered_data = filtered_data[(filtered_data["날짜_dt"] >= start_date) & (filtered_data["날짜_dt"] <= end_date)]

    if filtered_data.empty:
        st.warning(f"선택하신 조건에 해당하는 VOC 데이터가 없습니다.")
    else:
        st.header("🚀 핵심 지표 요약")
        st.markdown(f"**기간: {date_range[0].strftime('%Y-%m-%d')} ~ {date_range[1].strftime('%Y-%m-%d')}**")
        current_count = len(filtered_data)
        top3_categories = filtered_data['L2 태그'].value_counts().nlargest(3)
        col1, col2, col3 = st.columns(3)
        col1.metric("총 VOC 건수", f"{current_count} 건")
        # (전주 대비 로직은 생략)
        with col3:
            st.markdown("**주요 카테고리 TOP 3**")
            for i, (cat, count) in enumerate(top3_categories.items()):
                st.markdown(f"**{i+1}.** {cat} ({count}건)")
        
        st.markdown("---")
        
        tab_main, tab_search = st.tabs(["📊 카테고리 분석", "🔍 키워드 검색"])
        with tab_main:
            st.header("📅 일자별 VOC 발생 추이")
            all_days_in_range = pd.date_range(start=date_range[0], end=date_range[1], freq='D')
            range_df = pd.DataFrame(all_days_in_range, columns=['날짜_dt'])
            daily_counts = filtered_data.groupby(filtered_data['날짜_dt'].dt.date).size().reset_index(name="건수")
            daily_counts['날짜_dt'] = pd.to_datetime(daily_counts['날짜_dt'])
            merged_daily_data = pd.merge(range_df, daily_counts, on='날짜_dt', how='left').fillna(0)
            fig_daily_trend = px.line(
                merged_daily_data, x='날짜_dt', y='건수',
                title=f"<b>{date_range[0]} ~ {date_range[1]} 일자별 VOC 추이</b>",
                labels={'날짜_dt': '날짜', '건수': 'VOC 건수'}, markers=True, text="건수"
            )
            fig_daily_trend.update_traces(textposition="top center")
            fig_daily_trend.update_layout(xaxis_title="", yaxis_title="건수")
            st.plotly_chart(fig_daily_trend, use_container_width=True)

            st.header("📌 VOC 카테고리별 현황")
            l2_counts = filtered_data['L2 태그'].value_counts().nlargest(10).sort_values(ascending=True)
            fig_l2 = px.bar(
                l2_counts, x=l2_counts.values, y=l2_counts.index, orientation='h', 
                title="<b>태그별 현황 TOP 10</b>", labels={'x': '건수', 'y': '태그'}, text_auto=True
            )
            st.plotly_chart(fig_l2, use_container_width=True)
            
            st.subheader("📑 VOC 원본 데이터")
            col1, col2 = st.columns([3, 1])
            with col1:
                all_categories = sorted(filtered_data['L2 태그'].unique())
                selected_categories = st.multiselect("확인하고 싶은 카테고리를 선택하세요:", options=all_categories, default=top3_categories.index.tolist())
            
            if selected_categories:
                display_data = filtered_data[filtered_data['L2 태그'].isin(selected_categories)].copy()
                with col2:
                    st.text(" ") # 버튼 위치 조정을 위한 빈 공간
                    st.download_button(
                        label="📥 CSV로 다운로드",
                        data=display_data.to_csv(index=False).encode('utf-8-sig'),
                        file_name=f"voc_category_data_{datetime.now().strftime('%Y%m%d')}.csv",
                        mime="text/csv",
                        use_container_width=True
                    )
                display_data['문의내용_요약'] = display_data['문의내용_요약'].apply(mask_phone_number)
                display_df = display_data.rename(columns={'플랫폼': '구분', '문의내용_요약': '문의 내용'})
                st.dataframe(display_df[["구분", "날짜", "게임", "L2 태그", "상담제목", "문의 내용", "GSN(USN)", "기기정보"]], use_container_width=True, height=500)

        with tab_search:
            st.header("🔍 키워드 검색")
            search_keyword = st.text_input("분석하고 싶은 키워드를 입력하세요:", placeholder="예: 환불, 튕김, 업데이트...")
            if search_keyword:
                search_results = filtered_data[filtered_data["상담제목"].str.contains(search_keyword, na=False, case=False) | filtered_data["검색용_문의내용"].str.contains(search_keyword, na=False, case=False)].copy()
                if not search_results.empty:
                    st.success(f"✅ \"{search_keyword}\" 키워드가 포함된 VOC: **{len(search_results)}**건")
                    
                    st.subheader(f"'{search_keyword}' 키워드 검색 결과 추이")
                    search_all_days = pd.date_range(start=date_range[0], end=date_range[1], freq='D')
                    search_range_df = pd.DataFrame(search_all_days, columns=['날짜_dt'])
                    search_daily_counts = search_results.groupby(search_results['날짜_dt'].dt.date).size().reset_index(name="건수")
                    search_daily_counts['날짜_dt'] = pd.to_datetime(search_daily_counts['날짜_dt'])
                    search_merged_data = pd.merge(search_range_df, search_daily_counts, on='날짜_dt', how='left').fillna(0)
                    fig_search_trend = px.line(
                        search_merged_data, x='날짜_dt', y='건수',
                        title=f"<b>'{search_keyword}' 키워드 일자별 발생 추이</b>",
                        labels={'날짜_dt': '날짜', '건수': 'VOC 건수'}, markers=True, text="건수"
                    )
                    fig_search_trend.update_traces(textposition="top center")
                    fig_search_trend.update_layout(xaxis_title="", yaxis_title="건수")
                    st.plotly_chart(fig_search_trend, use_container_width=True)

                    search_results['감성'] = search_results['문의내용'].apply(classify_sentiment)
                    sentiment_counts = search_results['감성'].value_counts()
                    st.subheader("감성 분석 결과")
                    sentiment_cols = st.columns(3)
                    sentiment_cols[0].metric("긍정 😊", f"{sentiment_counts.get('긍정', 0)} 건")
                    sentiment_cols[1].metric("부정 😠", f"{sentiment_counts.get('부정', 0)} 건")
                    sentiment_cols[2].metric("중립 😐", f"{sentiment_counts.get('중립', 0)} 건")
                    
                    st.subheader("관련 VOC 목록")
                    st.download_button(
                        label="📥 검색 결과 다운로드",
                        data=search_results.to_csv(index=False).encode('utf-8-sig'),
                        file_name=f"voc_search_{search_keyword}_{datetime.now().strftime('%Y%m%d')}.csv",
                        mime="text/csv"
                    )
                    search_results['문의내용_요약'] = search_results['문의내용_요약'].apply(mask_phone_number)
                    display_search_df = search_results.rename(columns={'플랫폼': '구분', '문의내용_요약': '문의 내용'})
                    st.dataframe(display_search_df[["구분", "날짜", "게임", "L2 태그", "상담제목", "문의 내용", "GSN(USN)", "기기정보", "감성"]], use_container_width=True, height=400)
                    
                    st.subheader("연관 키워드 워드클라우드")
                    generate_wordcloud(search_results["문의내용"])
                else:
                    st.warning(f"⚠️ \"{search_keyword}\" 키워드가 포함된 VOC가 없습니다.")

