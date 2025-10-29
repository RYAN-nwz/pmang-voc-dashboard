# -*- coding: utf-8 -*-
import os
import re
import base64
from datetime import datetime, timedelta, date
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
# 1) 유틸 (이미지, URL/키 정규화)
# =============================
def get_image_as_base64(path: str):
    if os.path.exists(path):
        with open(path, "rb") as f:
            return base64.b64encode(f.read()).decode()
    return None

def _pure_url(v: str) -> str:
    """[text](https://...) 형식이면 실제 URL만 추출."""
    if not isinstance(v, str):
        return v
    v = v.strip()
    m = re.match(r"^\[[^\]]+\]\((https?://[^)]+)\)$", v)
    return m.group(1) if m else v

def normalize_sa_info(sa: dict) -> dict:
    """Secrets의 서비스계정 JSON을 정규화(마크다운 링크/줄바꿈)하여 반환."""
    sa = dict(sa or {})
    # URL 정규화
    sa["auth_uri"]  = _pure_url(sa.get("auth_uri", "")) or "https://accounts.google.com/o/oauth2/auth"
    sa["token_uri"] = _pure_url(sa.get("token_uri", "")) or "https://oauth2.googleapis.com/token"
    sa["auth_provider_x509_cert_url"] = _pure_url(sa.get("auth_provider_x509_cert_url", "")) or "https://www.googleapis.com/oauth2/v1/certs"
    # client_x509_cert_url 재생성(마크다운 흔적 방지)
    client_email = sa.get("client_email", "")
    if client_email:
        sa["client_x509_cert_url"] = f"https://www.googleapis.com/robot/v1/metadata/x509/{_urlquote(client_email)}"
    # private_key 줄바꿈 처리 (\n -> 실제 개행)
    pk = sa.get("private_key")
    if isinstance(pk, str) and "\\n" in pk and "\n" not in pk:
        sa["private_key"] = pk.replace("\\n", "\n")
    return sa

def now_kst_str():
    # KST 시간대를 사용하도록 명시적으로 정의됨
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")

def get_sheet_id() -> str:
    """Secrets 루트(SHEET_ID) 또는 [gcp_service_account].SHEET_ID에서 읽음."""
    sid = st.secrets.get("SHEET_ID", "")
    if not sid:
        sid = st.secrets.get("gcp_service_account", {}).get("SHEET_ID", "")
    return sid

# =============================
# 2) 로그인(OIDC) & 권한
# =============================
def require_login():
    """Streamlit Cloud OIDC 사용. 속성 부재에 안전하게 동작."""
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
# 3) Google Sheets 클라이언트/시트
# =============================
@st.cache_resource
def get_gspread_client():
    try:
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]
        # 배포(Secrets) 우선, 실패 시 로컬 파일
        creds = None
        try:
            sa_raw = st.secrets["gcp_service_account"]
            sa = normalize_sa_info(sa_raw)
            creds = service_account.Credentials.from_service_account_info(sa, scopes=scopes)
        except Exception:
            script_dir = os.path.dirname(os.path.abspath(__file__))
            SERVICE_ACCOUNT_FILE = os.path.join(script_dir, "service_account.json")
            if os.path.exists(SERVICE_ACCOUNT_FILE):
                creds = service_account.Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=scopes)
            else:
                st.error("인증 정보를 찾을 수 없습니다. (Secrets 또는 service_account.json)")
                st.stop()
            return gspread.authorize(creds)
        return gspread.authorize(creds)
    except Exception as e:
        st.error("Google 인증 실패: Secrets 또는 service_account.json 구성을 확인하세요.")
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

def get_or_create_user_mgmt_worksheet(ss):
    try:
        ws = ss.worksheet("user_management")
    except gspread.WorksheetNotFound:
        ws = ss.add_worksheet(title="user_management", rows=1000, cols=20)
        ws.append_row(["email", "name", "request_date", "status", "approved_date"])
    return ws

@st.cache_data(ttl=60)
def fetch_users_table(spreadsheet_id: str) -> pd.DataFrame:
    ss = open_sheet(spreadsheet_id)
    if not ss:
        return pd.DataFrame(columns=["email","name","request_date","status","approved_date"])
    ws = get_or_create_user_mgmt_worksheet(ss)
    try:
        rows = ws.get_all_records()
        df = pd.DataFrame(rows) if rows else pd.DataFrame()
        if df.empty:
            df = pd.DataFrame(columns=["email","name","request_date","status","approved_date"])
        return df
    except Exception as e:
        st.error("사용자 목록 로딩 오류")
        st.exception(e)
        return pd.DataFrame(columns=["email","name","request_date","status","approved_date"])

def submit_access_request(spreadsheet_id: str, email: str, name: str):
    ss = open_sheet(spreadsheet_id)
    if not ss:
        return
    ws = get_or_create_user_mgmt_worksheet(ss)
    df = fetch_users_table(spreadsheet_id)
    if not df.empty and (df["email"].str.lower() == email.lower()).any():
        st.info("이미 요청되었거나 등록된 이메일입니다.")
        return
    # KST 시간으로 기록
    ws.append_row([email, name, now_kst_str(), "pending", ""])
    st.success("접근 요청 완료! 관리자의 승인을 기다려주세요.")
    st.cache_data.clear()

def approve_user(spreadsheet_id: str, email: str):
    ss = open_sheet(spreadsheet_id)
    if not ss:
        return
    ws = get_or_create_user_mgmt_worksheet(ss)
    cell = ws.find(email)
    ws.update_cell(cell.row, 4, "approved")
    # KST 시간으로 기록
    ws.update_cell(cell.row, 5, now_kst_str())
    st.toast(f"{email} 승인 완료")
    st.cache_data.clear()
    st.rerun()

def revoke_user(spreadsheet_id: str, email: str):
    ss = open_sheet(spreadsheet_id)
    if not ss:
        return
    ws = get_or_create_user_mgmt_worksheet(ss)
    cell = ws.find(email)
    ws.delete_rows(cell.row)
    st.toast(f"{email} 권한 삭제 완료")
    st.cache_data.clear()
    st.rerun()

def is_approved(df_users: pd.DataFrame, email: str) -> bool:
    if df_users.empty:
        return False
    row = df_users.loc[df_users["email"].str.lower() == (email or "").lower()]
    return (not row.empty) and (row.iloc[0]["status"] == "approved")

# =============================
# 4) 데이터 처리
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

def extract_gsn_usn(row):
    platform = row.get('플랫폼', '')
    if platform in ['MOB', 'for kakao']:
        inquiry = str(row.get('문의내용', ''))
        m = re.search(r'회원번호\s*:\s*(\d+)', inquiry)
        if m: return m.group(1)
    if platform == 'PC':
        info = str(row.get('고객정보', ''))
        m = re.search(r'\d+', info)
        if m: return m.group(0)
    return ""

def extract_device_info(row):
    inquiry = str(row.get('문의내용', ''))
    m = re.search(r'휴대폰기기정보\s*:\s*(\S+)', inquiry)
    if m: return m.group(1)
    platform = row.get('플랫폼', '')
    if platform == 'PC': return 'PC'
    return ""

def truncate_inquiry_content(text):
    if isinstance(text, str):
        # 300자까지 자르고, 뒤에 있는 회원번호 정보를 제거
        cleaned = text.split("회원번호 :")[0].strip()
        return cleaned[:300] + ('...' if len(cleaned) > 300 else '')
    return ""

def classify_sentiment(text):
    if not isinstance(text, str): return "중립"
    pos = ["감사합니다", "좋아요", "도움이 되었습니다", "해결", "고맙습니다"]
    neg = ["짜증", "오류", "환불", "안돼요", "쓰레기", "조작", "불만", "문제", "패몰림", "오링", "강퇴", "버그", "렉"]
    t = text.lower()
    if any(k in t for k in [w.lower() for w in neg]): return "부정"
    if any(k in t for k in [w.lower() for w in pos]): return "긍정"
    return "중립"

@st.cache_data(ttl=600)
def load_voc_data(spreadsheet_id: str) -> pd.DataFrame:
    """
    월별 시트(YY-MM) 우선 로드. 없으면 기존 일별 시트도 읽어 임시 호환.
    각 행에는 반드시 '날짜'(YYMMDD) 컬럼이 있어야 함.
    """
    ss = open_sheet(spreadsheet_id)
    if not ss:
        return pd.DataFrame()
    try:
        all_data = []
        all_worksheets = ss.worksheets()

        # 월별 시트 필터
        monthly_sheet_titles = [ws.title for ws in all_worksheets if re.match(r'^\d{2}-\d{2}$', ws.title)]
        if monthly_sheet_titles:
            for t in monthly_sheet_titles:
                try:
                    ws = ss.worksheet(t)
                    rows = ws.get_all_records()
                    if rows:
                        all_data.extend(rows)
                except Exception:
                    continue
        else:
            # 임시: 일별 시트 호환
            for ws in all_worksheets:
                if ws.title.lower() in ["sheet1", "template", "mapping", "user_management"]:
                    continue
                if re.match(r'^\d{2}-\d{2}$', ws.title):
                    continue
                try:
                    rows = ws.get_all_records()
                    if rows:
                        # 일별 시트는 시트명이 YYMMDD라면 날짜로 사용
                        for r in rows:
                            r.setdefault("날짜", ws.title)
                        all_data.extend(rows)
                except Exception:
                    continue

        if not all_data:
            return pd.DataFrame()

        df = pd.DataFrame(all_data)

        required = ["접수번호","접수구분","접수일","처리자","처리일","접수 카테고리","처리 카테고리","고객정보","상담제목","문의내용","Summary","taglist","답변내용","날짜"]
        # 최소 핵심 컬럼만 강제 (실제 현황 맞춤)
        must = ["접수 카테고리","상담제목","문의내용","taglist","날짜"]
        if not all(col in df.columns for col in must):
            st.error(f"필수 컬럼 누락: {must}")
            return pd.DataFrame()

        # 타입 정리 (표시 안정성)
        for c in ["접수번호","접수구분","접수일","처리자","처리일","접수 카테고리","처리 카테고리","고객정보","상담제목","문의내용","Summary","taglist","답변내용","날짜"]:
            if c in df.columns:
                df[c] = df[c].astype(str)

        df = df.rename(columns={"taglist": "L2 태그"})
        df["게임"] = df["접수 카테고리"].apply(classify_game)
        df["플랫폼"] = df["접수 카테고리"].apply(classify_platform)

        # '날짜' = YYMMDD → datetime
        df["날짜_dt"] = pd.to_datetime(df["날짜"], format="%y%m%d", errors="coerce")
        df = df.dropna(subset=["날짜_dt"])

        # 타임존 (날짜만 있으므로 localize 후 convert)
        df["날짜_dt"] = df["날짜_dt"].dt.tz_localize("UTC").dt.tz_convert(KST)

        df["L1 태그"] = df["L2 태그"].map(L2_TO_L1_MAPPING).fillna("기타")
        df["GSN(USN)"] = df.apply(extract_gsn_usn, axis=1)
        df["기기정보"] = df.apply(extract_device_info, axis=1)
        # 문의내용 요약은 truncate 함수에서 처리 (마스킹은 나중에)
        df["문의내용_요약"] = df["문의내용"].apply(truncate_inquiry_content)  
        df["검색용_문의내용"] = df["문의내용_요약"]
        df["감성"] = df["문의내용"].apply(classify_sentiment)
        return df
    except Exception as e:
        st.error("VOC 데이터 로딩 오류")
        st.exception(e)
        return pd.DataFrame()

# 🚨 [수정된 함수] 게임별 전일 VOC 핵심 요약 및 샘플 추출
def get_yesterday_summary_by_game(voc_df: pd.DataFrame, current_date: date) -> dict:
    """전일 게임별 VOC 데이터를 분석하여 건수, 증감, 부정 비율, 핵심 VOC 샘플을 반환합니다."""
    
    if voc_df.empty or '날짜_dt' not in voc_df.columns:
        return {}

    yesterday = current_date - timedelta(days=1)
    two_days_ago = current_date - timedelta(days=2)
    
    # 🚨 [제외할 태그 목록 정의] - 밸런스/불만, 무료충전소/광고, 이벤트, 단순 문의/미분류 제외
    EXCLUDE_TAGS = ['밸런스/불만 (패몰림)', '광고/무료충전소', '이벤트', '단순 문의/미분류'] 
    
    GAME_ICONS = {"뉴맞고": "🎴", "섯다": "🎴", "포커": "♣️", "쇼다운홀덤": "♠️", "뉴베가스": "🎰"}
    games = list(GAME_ICONS.keys())
    results = {}
    
    # 1. 일별 VOC 건수 계산 (D-1, D-2)
    daily_counts = voc_df[voc_df["날짜_dt"].dt.date.isin([yesterday, two_days_ago])]
    daily_counts = daily_counts.groupby([daily_counts["날짜_dt"].dt.date, "게임"]).size().reset_index(name="count")
    
    counts_d1 = daily_counts[daily_counts["날짜_dt"] == yesterday].set_index("게임")["count"].to_dict()
    counts_d2 = daily_counts[daily_counts["날짜_dt"] == two_days_ago].set_index("게임")["count"].to_dict()

    for game in games:
        game_df_d1 = voc_df[(voc_df["날짜_dt"].dt.date == yesterday) & (voc_df["게임"] == game)].copy()
        
        count_d1 = counts_d1.get(game, 0)
        count_d2 = counts_d2.get(game, 0)
        
        # 증감 계산
        delta = count_d1 - count_d2
        
        # 전체 VOC 분석 (부정 VOC)
        neg_df_d1_all = game_df_d1[game_df_d1["감성"] == "부정"]
        
        # 🚨 [핵심 샘플 추출 시 제외할 VOC 필터링 (핵심 부정 VOC)]
        neg_df_d1_core = neg_df_d1_all[~neg_df_d1_all['L2 태그'].isin(EXCLUDE_TAGS)].copy()
        
        # 🚨 [수정] 분자: 핵심 부정 VOC 건수만 사용
        neg_count = len(neg_df_d1_core) 
        
        # 🚨 [핵심 VOC 건수(분모) 산정] - 전체 VOC 중 제외 태그 건수를 제외
        exclude_count = game_df_d1[game_df_d1['L2 태그'].isin(EXCLUDE_TAGS)].shape[0]
        core_voc_count = count_d1 - exclude_count 
        
        # 🚨 [neg_ratio 계산 수정] - 분모와 분자 모두 핵심 VOC 기준으로 계산
        neg_ratio = neg_count / core_voc_count * 100 if core_voc_count > 0 else 0
        
        # 핵심 VOC 샘플 추출 (부정 감성 VOC 중, 제외 태그가 아닌 것만 대상으로)
        sample_voc = {"제목": "VOC 없음", "내용": "---", "태그": "---", "인사이트": "전일 VOC 발생 기록 없음"}
        
        if not neg_df_d1_core.empty:
            # 핵심 부정 VOC 중 가장 문의내용이 긴 것 선택
            neg_df_d1_core['content_len'] = neg_df_d1_core['문의내용'].str.len()
            top_neg_voc = neg_df_d1_core.nlargest(1, 'content_len').iloc[0]
            
            sample_voc["제목"] = top_neg_voc['상담제목']
            sample_voc["내용"] = mask_phone_number(top_neg_voc['문의내용_요약']) # 마스킹 적용
            sample_voc["태그"] = top_neg_voc['L2 태그']
            
        elif not game_df_d1.empty:
            # 핵심 부정 VOC가 없을 경우, 전체 VOC에서 제외 태그가 아닌 것 중 가장 긴 것을 샘플로 사용
            game_df_d1_core = game_df_d1[~game_df_d1['L2 태그'].isin(EXCLUDE_TAGS)].copy()
            
            if not game_df_d1_core.empty:
                game_df_d1_core['content_len'] = game_df_d1_core['문의내용'].str.len()
                top_voc = game_df_d1_core.nlargest(1, 'content_len').iloc[0]
                sample_voc["제목"] = top_voc['상담제목']
                sample_voc["내용"] = mask_phone_number(top_voc['문의내용_요약'])
                sample_voc["태그"] = top_voc['L2 태그']
            else:
                 # 제외 태그가 아닌 VOC가 없는 경우
                sample_voc["인사이트"] = "전일 VOC는 있으나, 제외 태그 항목만 발생함"
                
        # 개선 인사이트 자동 생성 (키워드/비율 기반)
        if count_d1 > 0:
            if neg_ratio >= 30:
                tag_info = f"**{sample_voc['태그']}**" if sample_voc['태그'] != '---' else "주요 이슈"
                summary = f"🔥 심각: 부정 VOC {neg_ratio:.0f}%, {tag_info} 긴급 확인 필요"
            elif neg_ratio >= 10:
                tag_info = f"**{sample_voc['태그']}**" if sample_voc['태그'] != '---' else "주요 이슈"
                summary = f"⚠️ 주의: 부정 VOC {neg_ratio:.0f}%, {tag_info} 모니터링 필요"
            else:
                tag_info = f"**{sample_voc['태그']}**" if sample_voc['태그'] != '---' else ""
                summary = f"🟢 양호: 컨디션 안정, 주요 이슈 태그: {tag_info}"
            
            # VOC 샘플이 정상적으로 추출되지 않았을 경우, 인사이트 문구 조정
            if sample_voc["태그"] == "---" and sample_voc["인사이트"] == "전일 VOC는 있으나, 제외 태그 항목만 발생함":
                 summary = f"🟢 양호: 컨디션 안정. 발생 VOC는 주로 제외 항목(`밸런스/불만`, `단순 문의` 등)입니다."

            sample_voc["인사이트"] = summary
        
        results[game] = {
            "icon": GAME_ICONS[game],
            "count": count_d1,
            "delta": delta,
            "sample": sample_voc,
            "neg_ratio": neg_ratio
        }
    
    return results

# =============================
# 5) 차트
# =============================
def create_trend_chart(data, date_range, title):
    start_date, end_date = pd.to_datetime(date_range[0]), pd.to_datetime(date_range[1])
    all_days = pd.date_range(start=start_date, end=end_date, freq="D")
    range_df = pd.DataFrame(all_days, columns=["날짜_dt"])
    daily = data.groupby(data["날짜_dt"].dt.date).size().reset_index(name="건수")
    daily["날짜_dt"] = pd.to_datetime(daily["날짜_dt"])
    merged = pd.merge(range_df, daily, on="날짜_dt", how="left").fillna(0)
    merged["건수"] = merged["건수"].astype(int)
    fig = px.line(
        merged, x="날짜_dt", y="건수", title=f"<b>{title}</b>",
        labels={'날짜_dt': '날짜', '건수': 'VOC 건수'}, markers=True, text="건수"
    )
    fig.update_traces(textposition="top center")
    fig.update_layout(xaxis_title="", yaxis_title="건수", height=300)
    return fig

def create_donut_chart(data, title, group_by='L2 태그'):
    counts = data[group_by].value_counts()
    if len(counts) > 5:
        top4 = counts.nlargest(4)
        others = counts.iloc[4:].sum()
        chart_data = pd.concat([top4, pd.Series([others], index=["기타"])])
    else:
        chart_data = counts
    fig = go.Figure(data=[go.Pie(labels=chart_data.index, values=chart_data.values, hole=.6, textinfo='label+percent', insidetextorientation='radial')])
    fig.update_layout(title_text=f"<b>{title}</b>", showlegend=False, height=300, margin=dict(l=20, r=20, t=60, b=20))
    return fig

def clean_text_for_wordcloud(text):
    if not isinstance(text, str): return ""
    return re.sub(r'[^ㄱ-ㅎㅏ-ㅣ가-힣\s]', '', text)

def generate_wordcloud(text_series):
    texts = [clean_text_for_wordcloud(x) for x in text_series]
    s = " ".join(texts).strip()
    if not s:
        st.info("워드클라우드를 생성할 키워드가 충분하지 않습니다.")
        return
    font_rel = os.path.join("fonts", "NanumGothic.ttf")
    font_win = "c:/Windows/Fonts/malgun.ttf"
    font_path = font_rel if os.path.exists(font_rel) else (font_win if os.path.exists(font_win) else None)
    try:
        wc = WordCloud(font_path=font_path if font_path else None, width=400, height=200, background_color="white",
                         stopwords=set(['문의','게임','피망','고객','내용','확인','답변','부탁','처리','관련','안녕하세요'])).generate(s)
        fig, ax = plt.subplots(figsize=(4,2))
        ax.imshow(wc, interpolation="bilinear"); ax.axis("off")
        st.pyplot(fig)
    except Exception as e:
        st.error(f"워드클라우드 생성 오류: {e}")

def mask_phone_number(text):
    if not isinstance(text, str): return text
    # 010-xxxx-xxxx 패턴 마스킹
    return re.sub(r'(010[-.\s]?)\d{3,4}([-.\s]?)\d{4}', r'\1****\2****', text)

# =============================
# 6) MAIN
# =============================
def main():
    # 로그인
    require_login()
    me = current_user()
    if not me["email"]:
        st.error("구글 계정 이메일을 가져오지 못했습니다. 다시 로그인해 주세요.")
        st.button("로그아웃", on_click=st.logout)
        st.stop()

    # 헤더
    logo_b64 = get_image_as_base64(LOGO_IMAGE)
    if logo_b64:
        st.markdown(
            f'<div style="display:flex;align-items:center;margin-bottom:20px;">'
            f'<img src="data:image/png;base64,{logo_b64}" width="160" style="margin-right:12px;">'
            f'<h1 style="margin:0;">웹보드 VOC 대시보드</h1></div>', unsafe_allow_html=True
        )
    else:
        st.title("📊 웹보드 VOC 대시보드")

    st.sidebar.success(f"로그인: {me['name']} ({me['email']})")
    admin_email = st.secrets.get("app", {}).get("admin_email", "")
    is_admin = (me["email"].lower() == admin_email.lower())

    # 스프레드시트
    spreadsheet_id = get_sheet_id()
    if not spreadsheet_id:
        st.error("Secrets의 SHEET_ID 가 비어 있습니다. (루트 또는 [gcp_service_account] 내부)")
        st.stop()

    # 권한
    users_df = fetch_users_table(spreadsheet_id)
    if not (is_admin or is_approved(users_df, me["email"])):
        st.warning("이 페이지 접근 권한이 없습니다. 아래 버튼으로 접근을 요청해 주세요.")
        if st.button("접근 요청", use_container_width=True):
            submit_access_request(spreadsheet_id, me["email"], me["name"] or me["email"].split("@")[0])
        st.sidebar.button("로그아웃", on_click=st.logout)
        st.stop()

    # 데이터
    voc_df = load_voc_data(spreadsheet_id)

    # ------- 사이드바 -------
    with st.sidebar:
        st.markdown("---")
        st.subheader("📅 기간 선택")

        if voc_df.empty:
            st.warning("VOC 데이터가 없습니다.")
            date_range = None
        else:
            min_d = voc_df["날짜_dt"].min().date()
            max_d = voc_df["날짜_dt"].max().date()

            # 기본 7일
            default_start = max_d - timedelta(days=6)
            if default_start < min_d:
                default_start = min_d
            default_range = (default_start, max_d)

            # date_input: 세션 값 충돌 방지
            if "date_range" in st.session_state:
                date_range = st.date_input("조회 기간", key="date_range", min_value=min_d, max_value=max_d)
            else:
                date_range = st.date_input("조회 기간", value=default_range, key="date_range", min_value=min_d, max_value=max_d)

            # 퀵버튼
            col1, col2 = st.columns(2)
            def _set_days(d):
                new_start = max_d - timedelta(days=d-1)
                if new_start < min_d:
                    new_start = min_d
                st.session_state["date_range"] = (new_start, max_d)
            with col1:
                st.button("최근 7일", use_container_width=True, on_click=lambda:_set_days(7))
            with col2:
                st.button("최근 30일", use_container_width=True, on_click=lambda:_set_days(30))

        st.markdown("---")
        st.subheader("🕹️ 게임 및 플랫폼 선택")

        game_filters = {
            "뉴맞고": ["뉴맞고 (전체)", "뉴맞고 MOB", "뉴맞고 PC", "뉴맞고 for kakao"],
            "섯다": ["섯다 (전체)", "섯다 MOB", "섯다 PC", "섯다 for kakao"],
            "포커": ["포커 (전체)", "포커 MOB", "포커 PC", "포커 for kakao"],
            "쇼다운홀덤": ["쇼다운홀덤 (전체)", "쇼다운홀덤 MOB", "쇼다운홀덤 PC"],
            "뉴베가스": ["뉴베가스 (전체)", "뉴베가스 MOB", "뉴베가스 PC"],
            "기타": ["기타"],
        }
        all_options = [opt for sub in game_filters.values() for opt in sub]
        all_child = [opt for g, opts in game_filters.items() for opt in (opts[1:] if "(전체)" in opts[0] else opts)]

        # 전체 ON 기본값
        if "filters_initialized" not in st.session_state:
            st.session_state.filters_initialized = True
            st.session_state.select_all = True
            for opt in all_options:
                st.session_state[opt] = True

        def update_master_checkbox():
            all_groups = True
            all_solo = True
            for game, opts in game_filters.items():
                if len(opts) > 1:
                    # 그룹 전체 체크박스는 상태 유지
                    all_groups = all_groups and st.session_state.get(f"{game} (전체)", True)
                else:
                    all_solo = all_solo and st.session_state.get(opts[0], True)
            st.session_state.select_all = all_groups and all_solo

        def master_toggle():
            val = st.session_state.get("select_all", True)
            for opt in all_options:
                st.session_state[opt] = val

        def group_toggle(game_key):
            group_all = st.session_state.get(f"{game_key} (전체)", True)
            for opt in game_filters[game_key][1:]:
                st.session_state[opt] = group_all
            update_master_checkbox()

        def child_toggle(game_key):
            if len(game_filters[game_key]) > 1:
                all_children = all(st.session_state.get(opt, True) for opt in game_filters[game_key][1:])
                st.session_state[f"{game_key} (전체)"] = all_children
            update_master_checkbox()

        st.checkbox("전체", key="select_all", on_change=master_toggle, value=st.session_state.get("select_all", True))
        for game, opts in game_filters.items():
            with st.expander(game, expanded=True):
                if len(opts) > 1 and "(전체)" in opts[0]:
                    st.checkbox(opts[0], key=opts[0], on_change=group_toggle, args=(game,), value=st.session_state.get(opts[0], True))
                    for opt in opts[1:]:
                        st.checkbox(opt, key=opt, on_change=child_toggle, args=(game,), value=st.session_state.get(opt, True))
                else:
                    st.checkbox(opts[0], key=opts[0], on_change=update_master_checkbox, value=st.session_state.get(opts[0], True))

    # ------- 메인 -------
    if voc_df.empty or not date_range:
        st.warning("표시할 데이터가 없습니다. 필터/기간을 조정하세요.")
        st.sidebar.button("로그아웃", on_click=st.logout)
        return

    # 필터 적용
    selected = [opt for opt in all_child if st.session_state.get(opt, True)]
    
    # 선택된 항목이 없을 때
    if not selected:
        # 빈 결과로 즉시 view_df를 설정하여 에러를 피함 (이전 오류 해결 로직)
        filtered = pd.DataFrame(columns=voc_df.columns if not voc_df.empty else [])
        view_df = pd.DataFrame(columns=filtered.columns) # date_range 필터링을 건너뛰고 빈 상태로 설정
    else:
        conditions = []
        for opt in selected:
            if " for kakao" in opt:
                game_name = opt.replace(" for kakao", "")
                conditions.append((voc_df["게임"] == game_name) & (voc_df["플랫폼"] == "for kakao"))
            else:
                parts = opt.rsplit(" ", 1)
                game_name = parts[0]
                platform = parts[1] if len(parts) > 1 else None
                if platform:
                    conditions.append((voc_df["게임"] == game_name) & (voc_df["플랫폼"] == platform))
                else:
                    conditions.append(voc_df["게임"] == game_name)
        mask = pd.concat(conditions, axis=1).any(axis=1) if conditions else pd.Series(False, index=voc_df.index)
        filtered = voc_df[mask].copy()

        if not isinstance(date_range, (list, tuple)) or len(date_range) != 2:
            st.warning("표시할 데이터가 없습니다. 필터/기간을 조정하세요.")
            st.sidebar.button("로그아웃", on_click=st.logout)
            return

        # 날짜 필터링
        start_dt = pd.to_datetime(date_range[0]).date()
        end_dt = pd.to_datetime(date_range[1]).date()
        
        view_df = filtered[(filtered["날짜_dt"].dt.date >= start_dt) & (filtered["날짜_dt"].dt.date <= end_dt)].copy()

    if view_df.empty:
        st.warning("선택하신 조건에 해당하는 데이터가 없습니다.")
        st.sidebar.button("로그아웃", on_click=st.logout)
        return

    # ===== CSS 스타일 조정 (VOC 건수 폰트 크기 조정) =====
    st.markdown("""
        <style>
            /* metric value 폰트 크기 증가 */
            [data-testid="stMetricValue"] {
                font-size: 1.8rem; /* 기존보다 크게 설정 */
            }
            /* metric label 폰트 크기 증가 및 굵게 */
            [data-testid="stMetricLabel"] label {
                font-size: 1rem;
                font-weight: bold;
            }
        </style>
    """, unsafe_allow_html=True)
    # ===== CSS 스타일 조정 끝 =====

    # ===== 대시보드 상단 요약 (기간 전체 VOC 건수 제거) =====
    with st.container(border=True):
        
        # 1. 전일 VOC 컨디션 요약 및 심층 분석 (하나의 컨테이너로 시각적 그룹핑)
        current_kdate = datetime.now(KST).date()
        yesterday_date = current_kdate - timedelta(days=1)
        st.header(f"🚀 전일 VOC 컨디션 ({yesterday_date.strftime('%Y-%m-%d')})")
        
        # 🚨 [긴급도 기준 한 줄 추가]
        st.caption("**긴급도 기준:** '심각'은 부정 감성 VOC 30% 이상, '주의'는 부정 감성 VOC 10% 이상일 경우 표시됩니다. (비핵심 VOC 제외 기준)")
        
        game_summaries = get_yesterday_summary_by_game(voc_df, current_kdate)
        games_to_show = ["뉴맞고", "섯다", "포커", "쇼다운홀덤", "뉴베가스"]
        
        # 1-1. 게임별 요약 (5개 컬럼 메트릭)
        cols = st.columns(len(games_to_show))
        
        for i, game in enumerate(games_to_show):
            summary_data = game_summaries.get(game, {})
            
            if not summary_data:
                cols[i].caption(f"**{game}**")
                cols[i].write("데이터 없음")
                continue

            count = summary_data['count']
            delta_val = summary_data['delta']
            icon = summary_data['icon']
            
            # 메트릭 출력 (VOC 건수 및 전일 대비 증감)
            cols[i].metric(
                label=f"{icon} {game}", 
                value=f"{count} 건", 
                delta=f"{delta_val} 건" if delta_val != 0 else None,
                delta_color="inverse" if delta_val > 0 else "normal"
            )
            
            # 한 줄 요약 텍스트 (메트릭 바로 아래에 작게 표시)
            summary_text = summary_data['sample']['인사이트'].split(':')[0]
            
            color = "green"
            if "🔥 심각" in summary_text: color = "red"
            elif "⚠️ 주의" in summary_text: color = "orange"
            
            cols[i].markdown(f'<p style="color:{color}; font-size: 0.9em; margin-top: -10px;">{summary_text}</p>', unsafe_allow_html=True)
        
        st.markdown("---") # 요약 메트릭과 심층 분석 구분선

        # 1-2. 게임별 심층 분석 (Expander를 사용하여 깔끔하게)
        st.subheader(f"🔍 게임별 상세 이슈 분석")

        for game in games_to_show:
            summary_data = game_summaries.get(game, {})
            
            if not summary_data or summary_data['count'] == 0:
                continue

            sample = summary_data['sample']
            icon = summary_data['icon']
            
            # 🚨 [수정] 핵심 이슈 태그의 건수 계산
            core_tag = sample['태그']
            core_tag_count = 0
            if core_tag != '---':
                yesterday = current_kdate - timedelta(days=1)
                game_df_d1 = voc_df[(voc_df["날짜_dt"].dt.date == yesterday) & (voc_df["게임"] == game)]
                core_tag_count = game_df_d1[game_df_d1['L2 태그'] == core_tag].shape[0]

            # 🚨 [수정] Expander 제목에서 '전일 VOC' 항목 제거
            if core_tag_count > 0:
                # 핵심 태그 건수만 표시
                tag_count_info = f" | **{core_tag}**: {core_tag_count} 건"
            else:
                tag_count_info = ""
                
            # '전일 VOC: N 건' 제거하고, 게임명과 핵심 태그 건수, 인사이트만 유지
            expander_title = f"{icon} **{game}**{tag_count_info} | {sample['인사이트']}"
            
            with st.expander(expander_title):
                # 1. 핵심 VOC 샘플
                st.markdown(f"**주요 이슈 태그:** `{sample['태그']}`")
                st.markdown(f"**VOC 제목:** {sample['제목']}")
                
                # HTML 블록처럼 보이도록 구성
                st.markdown(f"""
                    <div style="border-left: 4px solid #F0F2F6; padding-left: 15px; margin: 15px 0; background-color: #FAFAFA; border-radius: 4px;">
                        <p style="font-style: italic; color: #555555; margin-bottom: 0;">
                            {sample['내용']}
                        </p>
                    </div>
                    """, unsafe_allow_html=True)
                
                # 2. 개선 인사이트
                st.markdown("---")
                st.markdown(f"**자동 분석 기반 개선 인사이트:**")
                
                # 부정 비율에 따른 자동 인사이트
                if summary_data['neg_ratio'] >= 30:
                    tag_info = f"**{sample['태그']}**" if sample['태그'] != '---' else "주요 이슈"
                    st.error(f"**긴급 대응** | 부정 VOC 비율 {summary_data['neg_ratio']:.0f}% (핵심 VOC 기준). {tag_info} 관련 이슈 발생 시, **영향도 파악 및 긴급 대응이 필요**합니다.")
                elif summary_data['neg_ratio'] >= 10:
                    tag_info = f"**{sample['태그']}**" if sample['태그'] != '---' else "주요 이슈"
                    st.warning(f"**집중 모니터링** | 부정 VOC 비율 {summary_data['neg_ratio']:.0f}% (핵심 VOC 기준). {tag_info} 관련 불만이 증가 추세일 수 있습니다. **해당 원본 VOC 검토를 시작**하세요.")
                else:
                    tag_info = f"**{sample['태그']}**" if sample['태그'] != '---' else ""
                    st.info(f"**정상 컨디션** | 부정 VOC 비율 {summary_data['neg_ratio']:.0f}% (핵심 VOC 기준). {tag_info} 관련 VOC는 일반적인 문의 수준입니다.")

    st.markdown("---")


    # ===== 탭 =====
    tabs = st.tabs(["📊 카테고리 분석", "🔍 키워드 검색", "💳 결제/인증 리포트"])

    # --- 탭1: 카테고리 분석 ---
    with tabs[0]:
        c1, c2 = st.columns(2)
        
        # 날짜 범위 설정 (기간 선택 사이드바를 활용)
        if not date_range:
            st.warning("유효한 조회 기간이 설정되지 않았습니다.")
        else:
            # 기간 설정 및 데이터프레임 필터링은 위에서 이미 view_df에 적용됨
            with c1:
                st.plotly_chart(create_trend_chart(view_df, (start_dt, end_dt), "일자별 VOC 발생 추이"), use_container_width=True)
            with c2:
                st.plotly_chart(create_donut_chart(view_df, "주요 L1 카테고리", group_by='L1 태그'), use_container_width=True)

        with st.container(border=True):
            st.header("📑 VOC 원본 데이터 (L2 태그 기준)")
            top5 = view_df["L2 태그"].value_counts().nlargest(5)
            all_cats = sorted(view_df["L2 태그"].unique())

            c1, c2 = st.columns([2, 1])
            with c1:
                selected_cats = st.multiselect("L2 태그 필터:", options=all_cats, default=top5.index.tolist())
            with c2:
                sentiment_options = ['긍정', '부정', '중립']
                selected_sentiments = st.multiselect("감성 필터:", options=sentiment_options, default=sentiment_options)

            if selected_cats and selected_sentiments:
                disp = view_df[view_df["L2 태그"].isin(selected_cats) & view_df['감성'].isin(selected_sentiments)].copy()
                # 표시 안정화
                for c in disp.columns:
                    disp[c] = disp[c].astype(str)
                disp["문의내용_요약"] = disp["문의내용_요약"].apply(mask_phone_number)
                show_df = disp.rename(columns={'플랫폼': '구분', '문의내용_요약': '문의 내용'})
                st.download_button(
                    "📥 CSV 다운로드",
                    data=disp.to_csv(index=False).encode("utf-8-sig"),
                    file_name=f"voc_category_{datetime.now(KST).strftime('%Y%m%d')}.csv",
                    mime="text/csv"
                )
                st.dataframe(
                    show_df[["구분","날짜","게임","L1 태그","L2 태그","상담제목","문의 내용","GSN(USN)","기기정보","감성"]].head(200),
                    use_container_width=True, height=500
                )

    # --- 탭2: 키워드 검색 ---
    with tabs[1]:
        st.header("🔍 키워드 검색")
        if "last_search_keyword" not in st.session_state:
            st.session_state.last_search_keyword = ""

        with st.form(key="search_form"):
            c1, c2 = st.columns([5,1])
            with c1:
                keyword = st.text_input(
                    "검색 키워드:",
                    value=st.session_state.get("last_search_keyword", ""),
                    placeholder="예: 환불, 튕김, 업데이트..."
                )
            with c2:
                st.write(""); st.write("")
                submitted = st.form_submit_button("검색", use_container_width=True)

        st.caption("여러 키워드는 콤마(,)로 구분하여 검색할 수 있습니다. (예: 환불,결제 → '환불' 또는 '결제' 포함)")

        if submitted:
            st.session_state.last_search_keyword = keyword

        last_keyword = st.session_state.get("last_search_keyword", "")
        if last_keyword:
            keywords = [re.escape(k.strip()) for k in last_keyword.split(",") if k.strip()]
            if keywords:
                search_regex = "|".join(keywords)
                r = view_df[
                    view_df["상담제목"].str.contains(search_regex, na=False, case=False, regex=True) |
                    view_df["검색용_문의내용"].str.contains(search_regex, na=False, case=False, regex=True)
                ].copy()

                if r.empty:
                    st.warning(f"'{last_keyword}' 키워드 결과 없음")
                else:
                    st.success(f"'{last_keyword}' 포함 VOC: {len(r)} 건")
                    r['문의내용_요약'] = r['문의내용_요약'].apply(mask_phone_number)

                    with st.container(border=True):
                        st.header("검색 결과 추이")
                        st.plotly_chart(create_trend_chart(r, (start_dt, end_dt), f"'{last_keyword}' 일자별 발생 추이"),
                                                             use_container_width=True)
                    with st.container(border=True):
                        st.header("관련 VOC 목록")
                        for c in r.columns:
                            r[c] = r[c].astype(str)
                        st.download_button(
                            "📥 검색 결과 다운로드",
                            data=r.to_csv(index=False).encode("utf-8-sig"),
                            file_name=f"voc_search_{last_keyword}_{datetime.now(KST).strftime('%Y%m%d')}.csv",
                            mime="text/csv"
                        )
                        disp_r = r.rename(columns={'플랫폼':'구분','문의내용_요약':'문의 내용'})
                        st.dataframe(
                            disp_r[["구분","날짜","게임","L2 태그","상담제목","문의 내용","GSN(USN)","기기정보","감성"]].head(200),
                            use_container_width=True, height=400
                        )
                    with st.container(border=True):
                        st.header("연관 키워드 워드클라우드")
                        generate_wordcloud(r["문의내용"])

    # --- 탭3: 결제/인증 리포트 ---
    with tabs[2]:
        st.header("💳 결제/인증 리포트")
        st.info("이 탭은 '계정'(로그인/인증) 및 '재화/결제'와 관련된 VOC만 필터링하여 보여줍니다.")
        payment_auth_df = view_df[view_df['L1 태그'].isin(['계정', '재화/결제'])].copy()

        if payment_auth_df.empty:
            st.warning("해당 기간에 결제 또는 인증 관련 VOC가 없습니다.")
        else:
            c1, c2 = st.columns(2)
            with c1:
                st.plotly_chart(create_trend_chart(payment_auth_df, (start_dt, end_dt), "결제/인증 관련 VOC 발생 추이"), use_container_width=True)
            with c2:
                l2_counts_payment = payment_auth_df["L2 태그"].value_counts().nlargest(10).sort_values(ascending=True)
                fig_l2_payment = px.bar(
                    l2_counts_payment, x=l2_counts_payment.values, y=l2_counts_payment.index, orientation='h',
                    title="<b>결제/인증 태그 현황 TOP 10</b>", labels={'x': '건수', 'y': '태그'}, text_auto=True
                )
                fig_l2_payment.update_layout(height=300)
                st.plotly_chart(fig_l2_payment, use_container_width=True)

            with st.container(border=True):
                st.header("📑 관련 VOC 원본 데이터")
                for c in payment_auth_df.columns:
                    payment_auth_df[c] = payment_auth_df[c].astype(str)
                disp_payment = payment_auth_df.rename(columns={'플랫폼': '구분', '문의내용_요약': '문의 내용'})
                st.dataframe(
                    disp_payment[["구분","날짜","게임","L1 태그","L2 태그","상담제목","문의 내용","GSN(USN)","기기정보","감성"]].head(200),
                    use_container_width=True, height=500
                )

    # ===== 어드민 멤버 관리 (최하단만) =====
    if is_admin:
        st.markdown("---")
        st.subheader("🛡️ 어드민 멤버 관리")
        users_df_latest = fetch_users_table(spreadsheet_id)
        tab_req, tab_members = st.tabs(["접근 요청 목록", "멤버 관리 목록"])

        with tab_req:
            pending = users_df_latest[users_df_latest["status"] == "pending"]
            if pending.empty:
                st.info("대기 중인 요청이 없습니다.")
            else:
                for _, r in pending.iterrows():
                    c1, c2, c3, c4 = st.columns([3,2,2,2])
                    c1.write(f"**{r['email']}**")
                    c2.write(r.get("name",""))
                    c3.write(r.get("request_date",""))
                    if c4.button("승인", key=f"approve_{r['email']}"):
                        approve_user(spreadsheet_id, r["email"])

        with tab_members:
            approved = users_df_latest[users_df_latest["status"] == "approved"]
            if approved.empty:
                st.info("승인된 멤버가 없습니다.")
            else:
                for _, r in approved.iterrows():
                    c1, c2, c3, c4, c5 = st.columns([3,2,2,2,1])
                    c1.write(f"**{r['email']}**")
                    c2.write(r.get("name",""))
                    c3.write(r.get("request_date",""))
                    c4.write(r.get("approved_date",""))
                    if c5.button("🗑️", key=f"revoke_{r['email']}"):
                        revoke_user(spreadsheet_id, r["email"])

    st.sidebar.button("로그아웃", on_click=st.logout)
    st.markdown("---")
    logo_b64 = get_image_as_base64(LOGO_IMAGE)
    if logo_b64:
        st.markdown(
            f'<div style="text-align:center;padding:20px 0;">'
            f'<img src="data:image/png;base64,{logo_b64}" width="90">'
            f'<p style="font-size:0.85rem;color:#6c757d;margin-top:8px;">© NEOWIZ Corp. All Rights Reserved.</p>'
            f'</div>',
            unsafe_allow_html=True
        )

if __name__ == "__main__":
    main()