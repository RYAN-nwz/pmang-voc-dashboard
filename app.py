# -*- coding: utf-8 -*-
import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime, timedelta
import gspread
from google.oauth2 import service_account # [수정] 최신 인증 라이브러리로 변경
from wordcloud import WordCloud
import matplotlib.pyplot as plt
import re
import os
import base64

# --- 1. 기본 설정 ---
LOGO_IMAGE = "images/pmang_logo.png"
st.set_page_config(page_title="웹보드 VOC 대시보드", page_icon=LOGO_IMAGE, layout="wide")

L2_TO_L1_MAPPING = {
    '로그인/인증': '계정', '정보 관리': '계정', '기술 오류': '시스템/환경',
    '결제 오류/미지급': '재화/결제', '환불/청약철회': '재화/결제', '재화 소실/오류': '재화/결제',
    '클래스/구독 상품': '재화/결제', '재화 정책/한도': '재화/결제', '밸런스/불만 (패몰림)': '게임 플레이',
    '콘텐츠 오류/문의': '게임 플레이', '토너먼트/대회': '게임 플레이', '점령전/거점전': '게임 플레이',
    '랭킹페스타': '게임 플레이', '연승챌린지': '게임 플레이', '패밀리게임': '게임 플레이',
    '광고/무료충전소': '이벤트/혜택', '이벤트': '이벤트/혜택', '비매너/욕설 신고': '불량 이용자',
    '제재 문의': '불량 이용자', '콘텐츠/시스템 건의': '정책/건의 (VOC)', '운영/정책 건의': '정책/건의 (VOC)',
    '단순 문의/미분류': '기타'
}

# --- 2. 데이터 처리 및 유틸리티 함수 ---

def classify_game(category):
    if pd.isna(category): return "기타"
    processed_category = re.sub(r'[^a-z0-9ㄱ-ㅎㅏ-ㅣ가-힣]', '', str(category).lower())
    if "쇼다운홀덤" in processed_category or "showdown" in processed_category: return "쇼다운홀덤"
    if "뉴베가스" in processed_category or "newvegas" in processed_category or "카지노군" in processed_category: return "뉴베가스"
    if "뉴맞고" in processed_category or "newmatgo" in processed_category: return "뉴맞고"
    if "섯다" in processed_category or "sutda" in processed_category: return "섯다"
    if "포커" in processed_category or "poker" in processed_category: return "포커"
    return "기타"

def classify_platform(category):
    if pd.isna(category): return "기타"
    processed_category = re.sub(r'[^a-z0-9ㄱ-ㅎㅏ-ㅣ가-힣]', '', str(category).lower())
    if "forkakao" in processed_category or "fork" in processed_category: return "for kakao"
    if "mob" in processed_category or "모바일" in processed_category: return "MOB"
    if "pc" in processed_category: return "PC"
    return "기타"

def extract_gsn_usn(row):
    platform = row.get('플랫폼', '')
    if platform in ['MOB', 'for kakao']:
        inquiry_content = str(row.get('문의내용', ''))
        gsn_match = re.search(r'회원번호\s*:\s*(\d+)', inquiry_content)
        if gsn_match: return gsn_match.group(1)
    if platform == 'PC':
        customer_info = str(row.get('고객정보', ''))
        usn_match = re.search(r'\d+', customer_info)
        if usn_match: return usn_match.group(0)
    return ""

def extract_device_info(row):
    inquiry_content = str(row.get('문의내용', ''))
    device_match = re.search(r'휴대폰기기정보\s*:\s*(\S+)', inquiry_content)
    if device_match: return device_match.group(1)
    platform = row.get('플랫폼', '')
    if platform == 'PC': return 'PC'
    return ""

def truncate_inquiry_content(text):
    if isinstance(text, str): return text.split("회원번호 :")[0].strip()
    return ""


@st.cache_data(ttl=600)
def load_data():
    """Google Sheets에서 모든 VOC 데이터를 불러오고 전처리하는 함수"""
    try:
        scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
        creds = None
        spreadsheet_id = "1rgR21yUBtKSJKE4KYdBvoZzDU-0jZ_wLb6Tqc_zS7RM" # 기본값
        
        # [오류 수정] 배포 환경(st.secrets)과 로컬 환경(json 파일) 모두 지원하도록 수정
        try:
            # 배포 환경: st.secrets에서 인증 정보 로드
            creds_dict = dict(st.secrets["gcp_service_account"])
            creds = service_account.Credentials.from_service_account_info(creds_dict, scopes=scopes)
            spreadsheet_id = st.secrets.get("SHEET_ID", spreadsheet_id)
        except (st.errors.StreamlitAPIException, FileNotFoundError, KeyError):
            # 로컬 환경: service_account.json 파일에서 인증 정보 로드
            SERVICE_ACCOUNT_FILE = "service_account.json"
            if os.path.exists(SERVICE_ACCOUNT_FILE):
                creds = service_account.Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=scopes)
            else:
                st.error(f"인증 정보를 찾을 수 없습니다. 프로젝트 폴더에 '{SERVICE_ACCOUNT_FILE}' 파일을 추가해주세요.")
                return pd.DataFrame()

        gc = gspread.authorize(creds)
        
        spreadsheet = gc.open_by_key(spreadsheet_id)
        
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
        df["플랫폼"] = df["접수 카테고리"].apply(classify_platform)
        df["날짜_dt"] = pd.to_datetime(df["날짜"], format='%y%m%d', errors='coerce')
        df = df.dropna(subset=["날짜_dt"])
        df['L1 태그'] = df['L2 태그'].map(L2_TO_L1_MAPPING).fillna('기타')
        df['GSN(USN)'] = df.apply(extract_gsn_usn, axis=1)
        df['기기정보'] = df.apply(extract_device_info, axis=1)
        df['문의내용_요약'] = df['문의내용'].apply(truncate_inquiry_content)
        df['검색용_문의내용'] = df['문의내용_요약']
        
        return df

    except gspread.exceptions.SpreadsheetNotFound:
        st.error("Google Sheets 문서를 찾을 수 없습니다. SPREADSHEET_ID를 확인하거나 Secrets에 올바르게 설정했는지 확인해주세요.")
        return pd.DataFrame()
    except Exception as e:
        st.error(f"데이터 로딩 중 예상치 못한 오류 발생: {e}")
        st.exception(e)
        return pd.DataFrame()

@st.cache_data(ttl=1800)
def get_game_issue_summary(_game_name, game_data):
    if game_data.empty: return {"title": "데이터 없음", "quote": "해당 기간에 수집된 VOC가 없습니다."}
    simulated_summaries = {
        "뉴맞고": {"title": "'부활하기' 기능 UI/UX 불만 지속 증가", "quote": "부활하기 버튼위치를 바꿔주던지 해야지 계속 실수로 눌러서 구슬 차감되네요"},
        "섯다": {"title": "재화 분리 정책에 대한 혼란", "quote": "섯다 머니가 포커 에서 섯다로 옮겨간 다음 포커 머니가 자동 복사 된 다고 하셨는데 자동 복사 기능이 어디에 있죠~? ...돈이 다른데 왜 돈이 다르죠~?"},
        "포커": {"title": "정기결제(클래스) 해지 문의 증가", "quote": "정기결제 해지 방법을 찾기 어렵다는 불만과 환불 요청이 지속적으로 발생하고 있습니다."},
        "뉴베가스": {"title": "불법 광고 필터링 강화 요청", "quote": "그래도 피망 대기업 아님? 광고가 불법광고가 나오내요 온라인 카지노 강원랜드라뇨..."},
        "쇼다운홀덤": {"title": "티켓 사용처 및 스케줄 개선 요청", "quote": "10만짜리 쿠폰은 출석 보상으로 왜 주는 거에요?ㅋㅋㅋ 새벽 4시 6시에만 10만 토너 열면서ㅋㅋㅋㅋㅋ"}
    }
    return simulated_summaries.get(_game_name, {"title": "분석 정보 없음", "quote": "-"})

def clean_text_for_wordcloud(text):
    if not isinstance(text, str): return ""
    return re.sub(r'[^ㄱ-ㅎㅏ-ㅣ가-힣\s]', '', text)

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
    font_path_relative = os.path.join('fonts', 'NanumGothic.ttf')
    font_path_windows = 'c:/Windows/Fonts/malgun.ttf'
    font_path = None
    if os.path.exists(font_path_relative): font_path = font_path_relative
    elif os.path.exists(font_path_windows): font_path = font_path_windows
    if font_path:
        korean_stopwords = ['문의', '게임', '피망', '고객', '내용', '확인', '답변', '부탁', '처리', '관련', '안녕하세요']
        try:
            wordcloud = WordCloud(font_path=font_path, width=400, height=200, background_color='white', stopwords=set(korean_stopwords)).generate(text)
            fig, ax = plt.subplots(figsize=(4, 2))
            ax.imshow(wordcloud, interpolation='bilinear')
            ax.axis('off')
            st.pyplot(fig)
        except Exception as e: st.error(f"워드클라우드 생성 중 오류 발생: {e}")
    else:
        st.warning("워드클라우드 생성을 위한 한글 폰트를 찾을 수 없습니다.")

def mask_phone_number(text):
    if not isinstance(text, str): return text
    return re.sub(r'(010[-.\s]?)\d{3,4}([-.\s]?)\d{4}', r'\1****\2****', text)
    
def create_trend_chart(data, date_range, title):
    start_date, end_date = pd.to_datetime(date_range[0]), pd.to_datetime(date_range[1])
    all_days_in_range = pd.date_range(start=start_date, end=end_date, freq='D')
    range_df = pd.DataFrame(all_days_in_range, columns=['날짜_dt'])
    daily_counts = data.groupby(data['날짜_dt'].dt.date).size().reset_index(name="건수")
    daily_counts['날짜_dt'] = pd.to_datetime(daily_counts['날짜_dt'])
    merged_data = pd.merge(range_df, daily_counts, on='날짜_dt', how='left').fillna(0)
    merged_data['건수'] = merged_data['건수'].astype(int)
    fig = px.line(merged_data, x='날짜_dt', y='건수', title=f"<b>{title}</b>", labels={'날짜_dt': '날짜', '건수': 'VOC 건수'}, markers=True, text="건수")
    fig.update_traces(textposition="top center")
    fig.update_layout(xaxis_title="", yaxis_title="건수", height=300)
    return fig

def create_donut_chart(data, title):
    category_counts = data['L2 태그'].value_counts()
    if len(category_counts) > 5:
        top_4 = category_counts.nlargest(4)
        others_count = category_counts.iloc[4:].sum()
        chart_data = top_4._append(pd.Series([others_count], index=['기타']))
    else: chart_data = category_counts
    fig = go.Figure(data=[go.Pie(labels=chart_data.index, values=chart_data.values, hole=.6, textinfo='label+percent', insidetextorientation='radial')])
    fig.update_layout(title_text=f"<b>{title}</b>", showlegend=False, height=300, margin=dict(l=20, r=20, t=60, b=20))
    return fig

def get_image_as_base64(path):
    if os.path.exists(path):
        with open(path, "rb") as image_file:
            return base64.b64encode(image_file.read()).decode()
    return None

# --- 3. UI 구성 ---
logo_base64 = get_image_as_base64(LOGO_IMAGE)
if logo_base64:
    st.markdown(f"""<div style="display: flex; align-items: center; margin-bottom: 20px;"><img src="data:image/png;base64,{logo_base64}" width="200" style="margin-right: 15px;"><h1 style="margin: 0; font-size: 2.5rem;">웹보드 VOC 대시보드</h1></div>""", unsafe_allow_html=True)
else: st.title("📊 웹보드 VOC 대시보드")

st.markdown("""<style> ... </style>""", unsafe_allow_html=True) # 기존 CSS 코드는 길어서 생략

st.markdown("---")
voc_data = load_data()
if voc_data.empty: st.warning("표시할 데이터가 없습니다. Google Sheets를 확인해주세요.")
else:
    # --- (이하 모든 UI 코드는 기존과 동일) ---
    with st.sidebar:
        st.title("💻 VOC 대시보드")
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
        
    if not selected_options:
        game_filtered_data = pd.DataFrame()
    else:
        conditions = []
        for option in selected_options:
            if " for kakao" in option:
                game_name = option.replace(" for kakao", "")
                platform_name = "for kakao"
                conditions.append((voc_data['게임'] == game_name) & (voc_data['플랫폼'] == platform_name))
            else:
                parts = option.rsplit(" ", 1)
                game_name = parts[0]
                platform_name = parts[1] if len(parts) > 1 else None

                if platform_name:
                    conditions.append((voc_data['게임'] == game_name) & (voc_data['플랫폼'] == platform_name))
                else: 
                    conditions.append(voc_data['게임'] == game_name)

        if conditions:
            final_condition = pd.concat(conditions, axis=1).any(axis=1)
            game_filtered_data = voc_data[final_condition].copy()
        else:
            game_filtered_data = pd.DataFrame()
            
    with st.sidebar:
        st.markdown("---")
        st.markdown("### 📅 기간 선택")
        
        if game_filtered_data.empty:
            st.warning("선택된 게임/플랫폼에 데이터가 없습니다.")
            date_range = (datetime.now().date() - timedelta(days=6), datetime.now().date())
        else:
            min_date_for_filter = game_filtered_data['날짜_dt'].min().date()
            max_date_for_filter = game_filtered_data['날짜_dt'].max().date()

            def set_date_range(days):
                start = max_date_for_filter - timedelta(days=days-1)
                if start < min_date_for_filter:
                    start = min_date_for_filter
                st.session_state.date_range = (start, max_date_for_filter)

            col1, col2 = st.columns(2)
            with col1: st.button("최근 7일", on_click=set_date_range, args=(7,), use_container_width=True)
            with col2: st.button("최근 30일", on_click=set_date_range, args=(30,), use_container_width=True)

            if 'date_range' not in st.session_state:
                set_date_range(7)
            else:
                if isinstance(st.session_state.date_range, (list, tuple)) and len(st.session_state.date_range) == 2:
                    start_state, end_state = st.session_state.date_range
                    start_state = max(start_state, min_date_for_filter)
                    end_state = min(end_state, max_date_for_filter)
                    if start_state > end_state:
                        start_state = end_state
                    st.session_state.date_range = (start_state, end_state)
                else:
                    set_date_range(7)

            date_range = st.date_input("조회 기간:", key='date_range', min_value=min_date_for_filter, max_value=max_date_for_filter)

    if game_filtered_data.empty or len(date_range) != 2:
        filtered_data = pd.DataFrame()
    else:
        start_date = pd.to_datetime(date_range[0])
        end_date = pd.to_datetime(date_range[1])
        filtered_data = game_filtered_data[(game_filtered_data["날짜_dt"] >= start_date) & (game_filtered_data["날짜_dt"] <= end_date)].copy()

    if filtered_data.empty:
        st.warning(f"선택하신 조건에 해당하는 VOC 데이터가 없습니다.")
    else:
        with st.container(border=True):
            st.header("🚀 핵심 지표 요약")
            st.markdown(f"**기간: {date_range[0].strftime('%Y-%m-%d')} ~ {date_range[1].strftime('%Y-%m-%d')}**")
            
            current_count = len(filtered_data)
            period_days = (date_range[1] - date_range[0]).days + 1
            prev_start_date = date_range[0] - timedelta(days=period_days)
            prev_end_date = date_range[1] - timedelta(days=period_days)
            prev_period_data = game_filtered_data[(game_filtered_data["날짜_dt"] >= pd.to_datetime(prev_start_date)) & (game_filtered_data["날짜_dt"] <= pd.to_datetime(prev_end_date))]
            prev_count = len(prev_period_data)
            delta = current_count - prev_count
            
            col1, col2 = st.columns([1, 2])
            with col1:
                delta_text = f"{delta} 건 (이전 동일 기간 대비)"
                st.metric("총 VOC 건수", f"{current_count} 건", delta_text, help=f"이전 기간: {prev_start_date.strftime('%Y-%m-%d')}~{prev_end_date.strftime('%Y-%m-%d')}")
            
            with col2:
                fig_donut = create_donut_chart(filtered_data, "주요 카테고리 TOP 5")
                st.plotly_chart(fig_donut, use_container_width=True)
            
            st.markdown("---")
            st.subheader("👪 게임별 주요 동향 (AI 요약)")
            st.info("아래 내용은 선택된 기간의 데이터를 기반으로 AI가 동적으로 생성한 요약입니다. (현재는 시뮬레이션)")

            game_list_for_summary = ["뉴맞고", "섯다", "포커", "뉴베가스", "쇼다운홀덤"]
            game_icons = {"뉴맞고": "🎴", "섯다": "🎴", "포커": "♣️", "뉴베가스": "🎰", "쇼다운홀덤": "♠️"}
            
            issue_cols = st.columns(5)
            for i, game_name in enumerate(game_list_for_summary):
                with issue_cols[i]:
                    game_data = filtered_data[filtered_data['게임'] == game_name]
                    summary = get_game_issue_summary(game_name, game_data)
                    st.markdown(f"""<div class="issue-card"><h5>{game_icons.get(game_name, "🃏")} {game_name}</h5><p><strong>{summary['title']}</strong></p><blockquote style="font-size: 0.9rem; color: #6c757d;">"{summary['quote']}"</blockquote></div>""", unsafe_allow_html=True)

        st.markdown("---")
        
        tab_main, tab_search = st.tabs(["### 📊 카테고리 분석", "### 🔍 키워드 검색"])
        
        with tab_main:
            col1, col2 = st.columns(2)
            with col1:
                with st.container(border=True):
                    fig_daily_trend = create_trend_chart(filtered_data, date_range, f"일자별 VOC 발생 추이")
                    st.plotly_chart(fig_daily_trend, use_container_width=True)
            with col2:
                with st.container(border=True):
                    l2_counts = filtered_data['L2 태그'].value_counts().nlargest(10).sort_values(ascending=True)
                    fig_l2 = px.bar(l2_counts, x=l2_counts.values, y=l2_counts.index, orientation='h', title="<b>태그별 현황 TOP 10</b>", labels={'x': '건수', 'y': '태그'}, text_auto=True)
                    fig_l2.update_layout(height=300)
                    st.plotly_chart(fig_l2, use_container_width=True)
            
            st.write("") 

            with st.container(border=True):
                st.header("📑 VOC 원본 데이터")
                col1, col2 = st.columns([3, 1])
                with col1:
                    top5_categories = filtered_data['L2 태그'].value_counts().nlargest(5)
                    all_categories = sorted(filtered_data['L2 태그'].unique())
                    selected_categories = st.multiselect("확인하고 싶은 카테고리를 선택하세요:", options=all_categories, default=top5_categories.index.tolist())
                
                if selected_categories:
                    display_data = filtered_data[filtered_data['L2 태그'].isin(selected_categories)].copy()
                    with col2:
                        st.text(" ") 
                        st.download_button(label="📥 CSV로 다운로드", data=display_data.to_csv(index=False).encode('utf-8-sig'), file_name=f"voc_category_data_{datetime.now().strftime('%Y%m%d')}.csv", mime="text/csv", use_container_width=True)
                    display_data['문의내용_요약'] = display_data['문의내용_요약'].apply(mask_phone_number)
                    display_df = display_data.rename(columns={'플랫폼': '구분', '문의내용_요약': '문의 내용'})
                    st.dataframe(display_df[["구분", "날짜", "게임", "L2 태그", "상담제목", "문의 내용", "GSN(USN)", "기기정보"]], use_container_width=True, height=500)

        with tab_search:
            st.header("🔍 키워드 검색")
            
            col1, col2 = st.columns([5, 1])
            with col1:
                search_keyword = st.text_input("검색하고 싶은 키워드를 입력하세요:", placeholder="예: 환불, 튕김, 업데이트...")
            with col2:
                st.write(" ")
                st.button("검색", use_container_width=True)

            if search_keyword:
                search_results = filtered_data[filtered_data["상담제목"].str.contains(search_keyword, na=False, case=False) | filtered_data["검색용_문의내용"].str.contains(search_keyword, na=False, case=False)].copy()
                if not search_results.empty:
                    st.success(f"✅ \"{search_keyword}\" 키워드가 포함된 VOC: **{len(search_results)}**건")
                    
                    search_results['감성'] = search_results['문의내용'].apply(classify_sentiment)

                    with st.container(border=True):
                        st.header(f"'{search_keyword}' 키워드 검색 결과 추이")
                        fig_search_trend = create_trend_chart(search_results, date_range, f"'{search_keyword}' 키워드 일자별 발생 추이")
                        st.plotly_chart(fig_search_trend, use_container_width=True)

                    with st.container(border=True):
                        st.header("관련 VOC 목록")
                        st.download_button(label="📥 검색 결과 다운로드", data=search_results.to_csv(index=False).encode('utf-8-sig'), file_name=f"voc_search_{search_keyword}_{datetime.now().strftime('%Y%m%d')}.csv", mime="text/csv")
                        search_results['문의내용_요약'] = search_results['문의내용_요약'].apply(mask_phone_number)
                        display_search_df = search_results.rename(columns={'플랫폼': '구분', '문의내용_요약': '문의 내용'})
                        st.dataframe(display_search_df[["구분", "날짜", "게임", "L2 태그", "상담제목", "문의 내용", "GSN(USN)", "기기정보", "감성"]], use_container_width=True, height=400)

                    st.write("") 
                    
                    col1, col2 = st.columns(2)
                    with col1:
                        with st.container(border=True):
                            st.header("감성 분석 결과")
                            sentiment_counts = search_results['감성'].value_counts()
                            sentiment_cols = st.columns(3)
                            sentiment_cols[0].metric("긍정 😊", f"{sentiment_counts.get('긍정', 0)} 건")
                            sentiment_cols[1].metric("부정 😠", f"{sentiment_counts.get('부정', 0)} 건")
                            sentiment_cols[2].metric("중립 😐", f"{sentiment_counts.get('중립', 0)} 건")
                    with col2:
                        with st.container(border=True):
                            st.header("연관 키워드 워드클라우드")
                            generate_wordcloud(search_results["문의내용"])

                else:
                    st.warning(f"⚠️ \"{search_keyword}\" 키워드가 포함된 VOC가 없습니다.")

    st.markdown("---")
    logo_base64 = get_image_as_base64(LOGO_IMAGE)
    if logo_base64:
        footer_html = f"""<div style="text-align: center; padding-top: 20px; padding-bottom: 20px;"><img src="data:image/png;base64,{logo_base64}" width="100"><p style="font-size: 0.8rem; color: #6c757d; margin-top: 10px;">© NEOWIZ Corp. All Rights Reserved.</p></div>"""
        st.markdown(footer_html, unsafe_allow_html=True)

