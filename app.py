# -*- coding: utf-8 -*-
import os
import re
import base64
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from urllib.parse import quote as _urlquote

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import gspread
from google.oauth2 import service_account
from wordcloud import WordCloud
import matplotlib.pyplot as plt

# =============================
# 0) 기본 설정
# =============================
LOGO_IMAGE = "images/pmang_logo.png"
st.set_page_config(page_title="웹보드 VOC 대시보드", page_icon=LOGO_IMAGE, layout="wide")
KST = ZoneInfo("Asia/Seoul")

# =============================
# 1) 유틸 함수
# =============================
def get_image_as_base64(path: str):
    if os.path.exists(path):
        with open(path, "rb") as f:
            return base64.b64encode(f.read()).decode()
    return None

def _pure_url(v: str) -> str:
    if not isinstance(v, str):
        return v
    v = v.strip()
    m = re.match(r"^\[[^\]]+\]\((https?://[^)]+)\)$", v)
    return m.group(1) if m else v

def normalize_sa_info(sa: dict) -> dict:
    sa = dict(sa or {})
    sa["auth_uri"]  = _pure_url(sa.get("auth_uri", "")) or "https://accounts.google.com/o/oauth2/auth"
    sa["token_uri"] = _pure_url(sa.get("token_uri", "")) or "https://oauth2.googleapis.com/token"
    sa["auth_provider_x509_cert_url"] = _pure_url(sa.get("auth_provider_x509_cert_url", "")) or "https://www.googleapis.com/oauth2/v1/certs"
    client_email = sa.get("client_email", "")
    if client_email:
        sa["client_x509_cert_url"] = f"https://www.googleapis.com/robot/v1/metadata/x509/{_urlquote(client_email)}"
    pk = sa.get("private_key")
    if isinstance(pk, str) and "\\n" in pk and "\n" not in pk:
        sa["private_key"] = pk.replace("\\n", "\n")
    return sa

def now_kst_str():
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")

def get_sheet_id() -> str:
    sid = st.secrets.get("SHEET_ID", "")
    if not sid:
        sid = st.secrets.get("gcp_service_account", {}).get("SHEET_ID", "")
    return sid

# =============================
# 2) 로그인 & 권한
# =============================
def require_login():
    try:
        is_logged_in = bool(getattr(st.user, "is_logged_in", False))
    except Exception:
        is_logged_in = False
    if not is_logged_in:
        st.title("🔐 로그인 필요")
        st.info("Google 계정으로 로그인 후 이용할 수 있습니다.")
        st.button("Google 계정으로 로그인", on_click=st.login, use_container_width=True)
        st.stop()

def current_user():
    def _g(obj, key, default=""):
        try:
            return getattr(obj, key, default) or default
        except Exception:
            return default
    return {
        "name": _g(st.user, "name", ""),
        "email": _g(st.user, "email", ""),
        "sub": _g(st.user, "sub", ""),
    }

# =============================
# 3) Google Sheets
# =============================
@st.cache_resource
def get_gspread_client():
    try:
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]
        sa_raw = st.secrets["gcp_service_account"]
        sa = normalize_sa_info(sa_raw)
        creds = service_account.Credentials.from_service_account_info(sa, scopes=scopes)
        return gspread.authorize(creds)
    except Exception as e:
        st.error("Google 인증 실패: Secrets의 gcp_service_account 구성을 확인하세요.")
        st.exception(e)
        return None

def open_sheet(spreadsheet_id: str):
    gc = get_gspread_client()
    if not gc:
        return None
    try:
        return gc.open_by_key(spreadsheet_id)
    except Exception as e:
        st.error("스프레드시트를 열 수 없습니다. (권한/ID 확인)")
        st.exception(e)
        return None

# =============================
# 4) VOC 데이터 처리
# =============================
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

def classify_game(category):
    if pd.isna(category): return "기타"
    processed = re.sub(r'[^a-z0-9ㄱ-ㅎㅏ-ㅣ가-힣]', '', str(category).lower())
    if "쇼다운홀덤" in processed or "showdown" in processed: return "쇼다운홀덤"
    if "뉴베가스" in processed or "newvegas" in processed or "카지노군" in processed: return "뉴베가스"
    if "뉴맞고" in processed or "newmatgo" in processed: return "뉴맞고"
    if "섯다" in processed or "sutda" in processed: return "섯다"
    if "포커" in processed or "poker" in processed: return "포커"
    return "기타"

def classify_platform(category):
    if pd.isna(category): return "기타"
    processed = re.sub(r'[^a-z0-9ㄱ-ㅎㅏ-ㅣ가-힣]', '', str(category).lower())
    if "forkakao" in processed or "fork" in processed: return "for kakao"
    if "mob" in processed or "모바일" in processed: return "MOB"
    if "pc" in processed: return "PC"
    return "기타"

@st.cache_data(ttl=600)
def load_voc_data(spreadsheet_id: str) -> pd.DataFrame:
    ss = open_sheet(spreadsheet_id)
    if not ss:
        return pd.DataFrame()
    try:
        all_data = []
        all_worksheets = ss.worksheets()

        # YY-MM / YYYY-MM / YY_MM / YYYY_MM 지원
        monthly_sheet_titles = [
            ws.title for ws in all_worksheets
            if re.match(r'^\d{2,4}[-_]\d{2}$', ws.title)
        ]

        if not monthly_sheet_titles:
            st.warning("월별 시트를 찾지 못했습니다. 데이터 구조를 확인하세요.")
            return pd.DataFrame()

        for sheet_title in monthly_sheet_titles:
            try:
                ws = ss.worksheet(sheet_title)
                rows = ws.get_all_records()
                if rows:
                    all_data.extend(rows)
            except Exception as e:
                st.warning(f"{sheet_title} 시트 로딩 오류: {e}")
                continue

        if not all_data:
            return pd.DataFrame()

        df = pd.DataFrame(all_data)
        required = ["접수 카테고리", "상담제목", "문의내용", "taglist"]
        if not all(col in df.columns for col in required):
            st.error(f"필수 컬럼 누락: {required}")
            return pd.DataFrame()

        df = df.rename(columns={"taglist": "L2 태그"})
        df["게임"] = df["접수 카테고리"].apply(classify_game)
        df["플랫폼"] = df["접수 카테고리"].apply(classify_platform)
        df["날짜_dt"] = pd.to_datetime(df["날짜"], format="%y%m%d", errors="coerce")
        df = df.dropna(subset=["날짜_dt"])
        df["날짜_dt"] = df["날짜_dt"].dt.tz_localize('UTC').dt.tz_convert(KST)
        df["L1 태그"] = df["L2 태그"].map(L2_TO_L1_MAPPING).fillna("기타")
        return df
    except Exception as e:
        st.error("VOC 데이터 로딩 오류")
        st.exception(e)
        return pd.DataFrame()

# =============================
# 5) 차트/뷰
# =============================
def create_trend_chart(data, date_range, title):
    start_date, end_date = pd.to_datetime(date_range[0]), pd.to_datetime(date_range[1])
    all_days = pd.date_range(start=start_date, end=end_date, freq="D")
    range_df = pd.DataFrame(all_days, columns=["날짜_dt"])
    daily = data.groupby(data["날짜_dt"].dt.date).size().reset_index(name="건수")
    daily["날짜_dt"] = pd.to_datetime(daily["날짜_dt"])
    merged = pd.merge(range_df, daily, on="날짜_dt", how="left").fillna(0)
    fig = px.line(merged, x="날짜_dt", y="건수", title=f"<b>{title}</b>", markers=True, text="건수")
    fig.update_traces(textposition="top center")
    fig.update_layout(height=300)
    return fig

def create_donut_chart(data, title, group_by='L2 태그'):
    counts = data[group_by].value_counts()
    if len(counts) > 5:
        top4 = counts.nlargest(4)
        others = counts.iloc[4:].sum()
        chart_data = pd.concat([top4, pd.Series([others], index=["기타"])])
    else:
        chart_data = counts
    fig = go.Figure(data=[go.Pie(labels=chart_data.index, values=chart_data.values, hole=.6)])
    fig.update_layout(title_text=f"<b>{title}</b>", height=300)
    return fig

# =============================
# 6) MAIN
# =============================
def main():
    require_login()
    me = current_user()
    spreadsheet_id = get_sheet_id()
    if not spreadsheet_id:
        st.error("SHEET_ID가 없습니다.")
        st.stop()

    users_df = pd.DataFrame()  # 접근 제어 생략 가능 (테스트 목적)

    voc_df = load_voc_data(spreadsheet_id)
    if voc_df.empty:
        st.warning("VOC 데이터를 불러오지 못했습니다.")
        st.stop()

    today = datetime.now(KST).date()
    date_range = (today - timedelta(days=6), today)

    st.title("📊 웹보드 VOC 대시보드")

    # 탭
    tabs = ["📊 카테고리 분석", "🔍 키워드 검색", "💳 결제/인증 리포트"]
    tab_main, tab_search, tab_payment = st.tabs(tabs)

    # MAIN
    with tab_main:
        st.header("일자별 VOC 발생 추이")
        st.plotly_chart(create_trend_chart(voc_df, date_range, "VOC 추이"), use_container_width=True)
        st.plotly_chart(create_donut_chart(voc_df, "주요 L1 카테고리", "L1 태그"), use_container_width=True)

    # SEARCH
    with tab_search:
        st.header("🔍 키워드 검색")
        keyword = st.text_input("검색어 입력", "")
        if keyword:
            filtered = voc_df[voc_df["문의내용"].str.contains(keyword, case=False, na=False)]
            st.success(f"{len(filtered)}건의 결과")
            st.dataframe(filtered)

    # PAYMENT
    with tab_payment:
        st.header("💳 결제/인증 리포트")
        payment_df = voc_df[voc_df["L1 태그"].isin(["계정", "재화/결제"])]
        st.plotly_chart(create_trend_chart(payment_df, date_range, "결제/인증 VOC 추이"), use_container_width=True)
        st.plotly_chart(create_donut_chart(payment_df, "결제/인증 TOP 5", "L2 태그"), use_container_width=True)

if __name__ == "__main__":
    main()
