# -*- coding: utf-8 -*-
import os, re, base64
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
def get_image_as_base64(path):
    if os.path.exists(path):
        with open(path, "rb") as f:
            return base64.b64encode(f.read()).decode()
    return None

def _pure_url(v):
    if not isinstance(v, str):
        return v
    v = v.strip()
    m = re.match(r"^\[[^\]]+\]\((https?://[^)]+)\)$", v)
    return m.group(1) if m else v

def normalize_sa_info(sa):
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

def get_sheet_id():
    sid = st.secrets.get("SHEET_ID", "")
    if not sid:
        sid = st.secrets.get("gcp_service_account", {}).get("SHEET_ID", "")
    return sid

# =============================
# 2) 로그인
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
    return {"name": _g(st.user, "name", ""), "email": _g(st.user, "email", ""), "sub": _g(st.user, "sub", "")}

# =============================
# 3) Google Sheets
# =============================
@st.cache_resource
def get_gspread_client():
    scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    sa = normalize_sa_info(st.secrets["gcp_service_account"])
    creds = service_account.Credentials.from_service_account_info(sa, scopes=scopes)
    return gspread.authorize(creds)

def open_sheet(spreadsheet_id):
    gc = get_gspread_client()
    return gc.open_by_key(spreadsheet_id)

@st.cache_data(ttl=600)
def load_voc_data(spreadsheet_id):
    ss = open_sheet(spreadsheet_id)
    if not ss: return pd.DataFrame()
    all_data = []
    for ws in ss.worksheets():
        t = ws.title
        if not re.match(r"^\d{2}-\d{2}$", t): continue
        rows = ws.get_all_records()
        if rows: all_data.extend(rows)
    if not all_data: return pd.DataFrame()
    df = pd.DataFrame(all_data)
    if "날짜" not in df.columns:
        st.error("시트에 '날짜' 컬럼이 없습니다.")
        return pd.DataFrame()
    df["날짜_dt"] = pd.to_datetime(df["날짜"], errors="coerce")
    df = df.dropna(subset=["날짜_dt"])
    df["날짜_dt"] = df["날짜_dt"].dt.tz_localize("UTC").dt.tz_convert(KST)
    return df

# =============================
# 4) 대시보드
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

def create_donut_chart(data, title, group_by):
    counts = data[group_by].value_counts()
    if len(counts) > 5:
        top4 = counts.nlargest(4)
        others = counts.iloc[4:].sum()
        chart_data = top4._append(pd.Series([others], index=["기타"]))
    else:
        chart_data = counts
    fig = go.Figure(data=[go.Pie(labels=chart_data.index, values=chart_data.values, hole=.6)])
    fig.update_layout(title_text=f"<b>{title}</b>", height=300)
    return fig

# =============================
# 5) MAIN
# =============================
def main():
    require_login()
    me = current_user()
    st.sidebar.success(f"로그인: {me['name']} ({me['email']})")
    spreadsheet_id = get_sheet_id()
    voc_df = load_voc_data(spreadsheet_id)
    st.title("📊 웹보드 VOC 대시보드")

    if voc_df.empty:
        st.warning("VOC 데이터가 없습니다.")
        return

    # 게임/플랫폼 필터 구조
    game_filters = {
        "뉴맞고": ["뉴맞고 (전체)", "뉴맞고 MOB", "뉴맞고 PC", "뉴맞고 for kakao"],
        "섯다": ["섯다 (전체)", "섯다 MOB", "섯다 PC", "섯다 for kakao"],
        "포커": ["포커 (전체)", "포커 MOB", "포커 PC", "포커 for kakao"],
        "쇼다운홀덤": ["쇼다운홀덤 (전체)", "쇼다운홀덤 MOB", "쇼다운홀덤 PC"],
        "뉴베가스": ["뉴베가스 (전체)", "뉴베가스 MOB", "뉴베가스 PC"],
        "기타": ["기타"],
    }
    all_options = [opt for sub in game_filters.values() for opt in sub]

    # ✅ 기본 전체 선택 상태로 초기화
    if 'filters_initialized' not in st.session_state or not st.session_state.get("select_all", False):
        for opt in all_options:
            st.session_state[opt] = True
        st.session_state.select_all = True
        st.session_state.filters_initialized = True

    # 사이드바 필터
    with st.sidebar:
        st.header("🕹️ 게임 및 플랫폼 선택")
        def master_toggle():
            val = st.session_state.get("select_all", False)
            for opt in all_options: st.session_state[opt] = val
        st.checkbox("전체", key="select_all", on_change=master_toggle)
        for game, opts in game_filters.items():
            with st.expander(game, expanded=True):
                for opt in opts:
                    st.checkbox(opt, key=opt)

    selected = [opt for opt in all_options if st.session_state.get(opt, False)]
    if not selected:
        st.warning("선택된 게임/플랫폼이 없습니다.")
        return

    filtered = voc_df.copy()  # 실제 게임별 필터는 이 아래에서 조건식 추가 가능
    min_d = filtered["날짜_dt"].min().date()
    max_d = filtered["날짜_dt"].max().date()
    default_range = (max_d - timedelta(days=6), max_d)
    st.sidebar.subheader("📅 기간 선택")
    date_range = st.sidebar.date_input("조회 기간", value=default_range, min_value=min_d, max_value=max_d)

    view_df = filtered[(filtered["날짜_dt"].dt.date >= date_range[0]) & (filtered["날짜_dt"].dt.date <= date_range[1])]
    if view_df.empty:
        st.warning("해당 조건의 데이터가 없습니다.")
        return

    # 메인 현황
    st.subheader(f"📆 VOC 현황 ({date_range[0]} ~ {date_range[1]})")
    col1, col2 = st.columns(2)
    with col1:
        st.plotly_chart(create_trend_chart(view_df, date_range, "일자별 VOC 발생 추이"), use_container_width=True)
    with col2:
        st.plotly_chart(create_donut_chart(view_df, "주요 카테고리 분포", group_by="taglist"), use_container_width=True)

    st.markdown("---")
    st.subheader("📋 원본 데이터 미리보기")
    st.dataframe(view_df, use_container_width=True, height=500)

if __name__ == "__main__":
    main()
