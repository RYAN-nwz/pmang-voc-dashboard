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

# =============================
# 기본 설정
# =============================
LOGO_IMAGE = "images/pmang_logo.png"
st.set_page_config(page_title="웹보드 VOC 대시보드", page_icon=LOGO_IMAGE, layout="wide")
KST = ZoneInfo("Asia/Seoul")

# =============================
# 유틸
# =============================
def normalize_sa_info(sa):
    sa = dict(sa or {})
    if isinstance(sa.get("private_key"), str) and "\\n" in sa["private_key"]:
        sa["private_key"] = sa["private_key"].replace("\\n", "\n")
    return sa

@st.cache_resource
def get_gspread_client():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
    sa = normalize_sa_info(st.secrets["gcp_service_account"])
    creds = service_account.Credentials.from_service_account_info(sa, scopes=scopes)
    return gspread.authorize(creds)

def open_sheet(spreadsheet_id):
    gc = get_gspread_client()
    return gc.open_by_key(spreadsheet_id)

@st.cache_data(ttl=600)
def load_voc_data(spreadsheet_id):
    ss = open_sheet(spreadsheet_id)
    all_data = []
    for ws in ss.worksheets():
        title = ws.title.strip()
        if not re.match(r"^\d{2}-\d{2}$", title):  # 월별 시트만 (예: 25-10)
            continue
        rows = ws.get_all_records()
        if rows:
            all_data.extend(rows)

    if not all_data:
        st.warning("VOC 데이터를 찾을 수 없습니다.")
        return pd.DataFrame()

    df = pd.DataFrame(all_data)
    if "날짜" not in df.columns:
        st.error("시트에 '날짜' 컬럼이 없습니다. (자동화 스크립트 확인 필요)")
        return pd.DataFrame()

    # 날짜 파싱 및 시간대 적용
    df["날짜_dt"] = pd.to_datetime(df["날짜"], errors="coerce")
    df = df.dropna(subset=["날짜_dt"])
    df["날짜_dt"] = df["날짜_dt"].dt.tz_localize("UTC").dt.tz_convert(KST)

    return df

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
# 메인
# =============================
def main():
    st.title("📊 웹보드 VOC 대시보드")

    spreadsheet_id = st.secrets.get("gcp_service_account", {}).get("SHEET_ID", "")
    if not spreadsheet_id:
        st.error("SHEET_ID가 설정되어 있지 않습니다.")
        st.stop()

    voc_df = load_voc_data(spreadsheet_id)
    if voc_df.empty:
        st.warning("VOC 데이터가 없습니다.")
        st.stop()

    # 🎮 게임/플랫폼 필터
    game_filters = {
        "뉴맞고": ["뉴맞고 (전체)", "뉴맞고 MOB", "뉴맞고 PC", "뉴맞고 for kakao"],
        "섯다": ["섯다 (전체)", "섯다 MOB", "섯다 PC", "섯다 for kakao"],
        "포커": ["포커 (전체)", "포커 MOB", "포커 PC", "포커 for kakao"],
        "쇼다운홀덤": ["쇼다운홀덤 (전체)", "쇼다운홀덤 MOB", "쇼다운홀덤 PC"],
        "뉴베가스": ["뉴베가스 (전체)", "뉴베가스 MOB", "뉴베가스 PC"],
        "기타": ["기타"],
    }
    all_options = [opt for sub in game_filters.values() for opt in sub]

    # ✅ 전체 선택 기본값 (처음부터 선택되어 있게)
    if 'filters_initialized' not in st.session_state or not st.session_state.get("select_all", False):
        for opt in all_options:
            st.session_state[opt] = True
        st.session_state.select_all = True
        st.session_state.filters_initialized = True

    # 🧩 사이드바
    with st.sidebar:
        st.header("🕹️ 게임 및 플랫폼 선택")

        def master_toggle():
            val = st.session_state.get("select_all", False)
            for opt in all_options:
                st.session_state[opt] = val

        st.checkbox("전체", key="select_all", on_change=master_toggle)

        for game, opts in game_filters.items():
            with st.expander(game, expanded=True):
                for opt in opts:
                    st.checkbox(opt, key=opt)

    selected = [opt for opt in all_options if st.session_state.get(opt, False)]
    if not selected:
        st.warning("선택된 게임/플랫폼이 없습니다.")
        st.stop()

    filtered = voc_df.copy()

    # 날짜 범위
    min_d = filtered["날짜_dt"].min().date()
    max_d = filtered["날짜_dt"].max().date()

    default_start = max(min_d, max_d - timedelta(days=6))
    default_range = (default_start, max_d)

    st.sidebar.subheader("📅 기간 선택")
    date_range = st.sidebar.date_input(
        "조회 기간",
        value=default_range,
        min_value=min_d,
        max_value=max_d
    )

    # 안전 보정
    if isinstance(date_range, tuple):
        start_d, end_d = date_range
    else:
        start_d, end_d = (date_range, date_range)
    start_d = max(min_d, min(start_d, max_d))
    end_d = min(max_d, max(start_d, end_d))
    date_range = (start_d, end_d)

    view_df = filtered[
        (filtered["날짜_dt"].dt.date >= date_range[0]) &
        (filtered["날짜_dt"].dt.date <= date_range[1])
    ]
    if view_df.empty:
        st.warning("해당 기간에 데이터가 없습니다.")
        st.stop()

    # =============================
    # 메인 대시보드
    # =============================
    st.subheader(f"📆 VOC 현황 ({date_range[0]} ~ {date_range[1]})")

    col1, col2 = st.columns(2)
    with col1:
        st.plotly_chart(create_trend_chart(view_df, date_range, "일자별 VOC 발생 추이"), use_container_width=True)
    with col2:
        st.plotly_chart(create_donut_chart(view_df, "주요 카테고리 분포", group_by="taglist"), use_container_width=True)

    st.markdown("---")
    st.subheader("📋 VOC 데이터 미리보기")

    # 🔒 PyArrow OverflowError 방지
    safe_df = view_df.copy()
    for c in safe_df.columns:
        try:
            safe_df[c] = safe_df[c].astype(str)
        except Exception:
            safe_df[c] = safe_df[c].apply(lambda x: str(x) if not pd.isna(x) else "")

    st.dataframe(safe_df, use_container_width=True, height=500)

if __name__ == "__main__":
    main()
