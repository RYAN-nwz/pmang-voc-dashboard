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
        # Cloud OIDC 로그인 버튼
        st.button("Google 계정으로 로그인", on_click=st.login, use_container_width=True)
        st.stop()

def current_user():
    # Streamlit 1.42 내장 사용자 컨텍스트 - 안전 접근
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
# 4) 대시보드: 데이터 처리 함수
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
        return text.split("회원번호 :")[0].strip()
    return ""

def classify_sentiment(text):
    if not isinstance(text, str): return "중립"
    pos = ["감사합니다", "좋아요", "도움이 되었습니다", "해결", "고맙습니다"]
    neg = ["짜증", "오류", "환불", "안돼요", "쓰레기", "조작", "불만", "문제", "패몰림", "오링"]
    t = text.lower()
    if any(k in t for k in [w.lower() for w in neg]): return "부정"
    if any(k in t for k in [w.lower() for w in pos]): return "긍정"
    return "중립"

@st.cache_data(ttl=600)
def load_voc_data(spreadsheet_id: str) -> pd.DataFrame:
    """
    [수정] Google Sheets에서 '월별' 시트의 VOC 데이터를 불러옵니다.
    YY-MM 형식의 모든 시트를 읽어와 성능과 확장성을 모두 확보합니다.
    """
    ss = open_sheet(spreadsheet_id)
    if not ss:
        return pd.DataFrame()
    try:
        all_data = []
        
        # [수정] '월별 시트' 아키텍처로 변경
        all_worksheets = ss.worksheets()
        
        # 'YY-MM' 형식의 시트 제목만 필터링
        monthly_sheet_titles = []
        for ws in all_worksheets:
            title = ws.title
            if re.match(r'^\d{2}-\d{2}$', title): # '25-09', '25-10' 등
                monthly_sheet_titles.append(title)
        
        if not monthly_sheet_titles:
            st.error("데이터가 없습니다. 'YY-MM' (예: 25-10) 형식의 월별 시트가 있는지 확인해주세요.")
            return pd.DataFrame()
            
        st.sidebar.info(f"데이터 로딩 중... (총 {len(monthly_sheet_titles)}개 월)")
        
        for sheet_title in monthly_sheet_titles:
            # 'user_management' 등은 'YY-MM' 형식이 아니므로 자동 제외됨
            try:
                ws = ss.worksheet(sheet_title) # 이름으로 시트 열기 시도
                rows = ws.get_all_records()
                if rows:
                    all_data.extend(rows)
            except gspread.WorksheetNotFound:
                continue # 있을 수 없는 일이지만, 안전장치
            except Exception as e:
                st.warning(f"'{sheet_title}' 시트 로딩 중 오류: {e}")
                continue
        
        if not all_data:
            return pd.DataFrame()
        
        df = pd.DataFrame(all_data)
        
        # [수정] '날짜' 컬럼이 자동화 스크립트에서 이미 제공된다고 가정
        required = ["접수 카테고리", "상담제목", "문의내용", "taglist", "날짜"]
        if not all(col in df.columns for col in required):
            st.error(f"필수 컬럼 누락: {required}. 자동화 스크립트가 '날짜' 컬럼을 추가했는지 확인하세요.")
            return pd.DataFrame()
            
        df = df.rename(columns={"taglist": "L2 태그"})
        df["게임"] = df["접수 카테고리"].apply(classify_game)
        df["플랫폼"] = df["접수 카테고리"].apply(classify_platform)
        
        # [수정] '날짜' 컬럼 형식이 YYMMDD로 저장되었다고 가정
        df["날짜_dt"] = pd.to_datetime(df["날짜"], format="%y%m%d", errors="coerce")
        df = df.dropna(subset=["날짜_dt"])
        
        # [수정] KST 시간대 정보 추가 (날짜만 있는 데이터이므로 tz_localize 대신)
        df['날짜_dt'] = df['날짜_dt'].dt.tz_localize('UTC').dt.tz_convert(KST)

        df["L1 태그"] = df["L2 태그"].map(L2_TO_L1_MAPPING).fillna("기타")
        df["GSN(USN)"] = df.apply(extract_gsn_usn, axis=1)
        df["기기정보"] = df.apply(extract_device_info, axis=1)
        df["문의내용_요약"] = df["문의내용"].apply(truncate_inquiry_content)
        df["검색용_문의내용"] = df["문의내용_요약"]
        df["감성"] = df["문의내용"].apply(classify_sentiment)
        return df
    except Exception as e:
        st.error("VOC 데이터 로딩 오류")
        st.exception(e)
        return pd.DataFrame()

# =============================
# 5) 대시보드: 차트/뷰
# =============================
def create_trend_chart(data, date_range, title):
    start_date, end_date = pd.to_datetime(date_range[0]), pd.to_datetime(date_range[1])
    all_days = pd.date_range(start=start_date, end=end_date, freq="D")
    range_df = pd.DataFrame(all_days, columns=["날짜_dt"])
    # [수정] KST 변환된 날짜_dt에서 date()를 추출하여 그룹화
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

def create_donut_chart(data, title, group_by='L2 태그'): # [추가] group_by 파라미터
    counts = data[group_by].value_counts()
    if len(counts) > 5:
        top4 = counts.nlargest(4)
        others = counts.iloc[4:].sum()
        chart_data = top4._append(pd.Series([others], index=["기타"]))
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
    if not font_path:
        st.warning("한글 폰트를 찾을 수 없어 기본 글꼴로 생성합니다.")
    try:
        wc = WordCloud(font_path=font_path, width=400, height=200, background_color="white",
                       stopwords=set(['문의','게임','피망','고객','내용','확인','답변','부탁','처리','관련','안녕하세요'])).generate(s)
        fig, ax = plt.subplots(figsize=(4,2))
        ax.imshow(wc, interpolation="bilinear"); ax.axis("off")
        st.pyplot(fig)
    except Exception as e:
        st.error(f"워드클라우드 생성 오류: {e}")

def mask_phone_number(text):
    if not isinstance(text, str): return text
    return re.sub(r'(010[-.\s]?)\d{3,4}([-.\s]?)\d{4}', r'\1****\2****', text)

# =============================
# 6) MAIN
# =============================
def main():
    # 6-1) 로그인 및 사용자 컨텍스트
    require_login()
    me = current_user()
    if not me["email"]:
        st.error("구글 계정 이메일을 가져오지 못했습니다. 다시 로그인해 주세요.")
        st.button("로그아웃", on_click=st.logout)
        st.stop()

    # 사이드 헤더
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

    # 6-2) 스프레드시트 ID
    spreadsheet_id = get_sheet_id()
    if not spreadsheet_id:
        st.error("Secrets의 SHEET_ID 가 비어 있습니다. (루트 또는 [gcp_service_account] 내부)")
        st.stop()

    # 6-3) 접근 권한 확인
    users_df = fetch_users_table(spreadsheet_id)
    if not (is_admin or is_approved(users_df, me["email"])):
        st.warning("이 페이지 접근 권한이 없습니다. 아래 버튼으로 접근을 요청해 주세요.")
        if st.button("접근 요청", use_container_width=True):
            submit_access_request(spreadsheet_id, me["email"], me["name"] or me["email"].split("@")[0])
        st.sidebar.button("로그아웃", on_click=st.logout)
        st.stop()

    # 6-4) VOC 데이터 로딩
    voc_df = load_voc_data(spreadsheet_id)
    
    filtered = pd.DataFrame()
    date_range = (datetime.now(KST).date() - timedelta(days=6), datetime.now(KST).date())

    # 6-5) 사이드바 필터
    with st.sidebar:
        st.markdown("---")
        
        st.subheader("📅 기간 선택")
        
        if voc_df.empty:
            st.warning("VOC 데이터가 없습니다.")
        else:
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

            if 'filters_initialized' not in st.session_state:
                st.session_state.filters_initialized = True
                for opt in all_options:
                    st.session_state[opt] = True
                st.session_state.select_all = True

            def master_toggle():
                val = st.session_state.get("select_all", False)
                for opt in all_options: st.session_state[opt] = val
            def group_toggle(game_key):
                group_all = st.session_state.get(f"{game_key} (전체)", False)
                for opt in game_filters[game_key][1:]: st.session_state[opt] = group_all
                update_master_checkbox()
            def child_toggle(game_key):
                if len(game_filters[game_key]) > 1:
                    all_children = all(st.session_state.get(opt, False) for opt in game_filters[game_key][1:])
                    st.session_state[f"{game_key} (전체)"] = all_children
                update_master_checkbox()
            def update_master_checkbox():
                all_groups = all(st.session_state.get(f"{g} (전체)", False) for g, opts in game_filters.items() if len(opts)>1)
                all_solo = all(st.session_state.get(opts[0], False) for g, opts in game_filters.items() if len(opts)==1)
                st.session_state.select_all = all_groups and all_solo

            selected = [opt for opt in all_child if st.session_state.get(opt, False)]
            
            if not selected:
                filtered = pd.DataFrame()
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
                if conditions:
                    mask = pd.concat(conditions, axis=1).any(axis=1)
                    filtered = voc_df[mask].copy()
                else:
                    filtered = pd.DataFrame()
            
            if filtered.empty:
                date_range = (datetime.now(KST).date() - timedelta(days=6), datetime.now(KST).date())
                st.warning("선택된 조건 데이터가 없습니다. 기간은 최근 7일로 표기됩니다.")
            else:
                min_d = filtered["날짜_dt"].min().date()
                max_d = filtered["날짜_dt"].max().date()

                def set_range(days):
                    start = max_d - timedelta(days=days-1)
                    if start < min_d: start = min_d
                    st.session_state.date_range = (start, max_d)

                col1, col2 = st.columns(2)
                with col1: st.button("최근 7일", on_click=set_range, args=(7,), use_container_width=True)
                with col2: st.button("최근 30일", on_click=set_range, args=(30,), use_container_width=True)

                if "date_range" not in st.session_state:
                    set_range(7)
                
                current_range = st.session_state.get("date_range")
                if not (isinstance(current_range, (list, tuple)) and len(current_range) == 2 and current_range[0] >= min_d and current_range[1] <= max_d):
                    set_range(7) 

                date_range = st.date_input("조회 기간:", key="date_range", min_value=min_d, max_value=max_d)

            st.markdown("---")
            st.subheader("🕹️ 게임 및 플랫폼 선택")
            st.checkbox("전체", key="select_all", on_change=master_toggle)
            for game, opts in game_filters.items():
                with st.expander(game, expanded=True):
                    if len(opts) > 1 and "(전체)" in opts[0]:
                        st.checkbox(opts[0], key=opts[0], on_change=group_toggle, args=(game,))
                        for opt in opts[1:]:
                            st.checkbox(opt, key=opt, on_change=child_toggle, args=(game,))
                    else:
                        st.checkbox(opts[0], key=opts[0], on_change=update_master_checkbox)

    
    if filtered.empty or not isinstance(date_range, (list, tuple)) or len(date_range) != 2:
        st.warning("표시할 데이터가 없습니다. 필터/기간을 조정하세요.")
    else:
        start_dt = pd.to_datetime(date_range[0]).date()
        end_dt = pd.to_datetime(date_range[1]).date()
        view_df = filtered[(filtered["날짜_dt"].dt.date >= start_dt) & (filtered["날짜_dt"].dt.date <= end_dt)].copy()

        if view_df.empty:
            st.warning("선택하신 조건에 해당하는 데이터가 없습니다.")
        else:
            with st.container(border=True):
                st.header("🚀 핵심 지표 요약")
                st.markdown(f"**기간: {date_range[0].strftime('%Y-%m-%d')} ~ {date_range[1].strftime('%Y-%m-%d')}**")

                period_days = (date_range[1] - date_range[0]).days + 1
                prev_start = date_range[0] - timedelta(days=period_days)
                prev_end   = date_range[1] - timedelta(days=period_days)
                prev_df = filtered[(filtered["날짜_dt"].dt.date >= prev_start) & (filtered["날짜_dt"].dt.date <= prev_end)]
                delta = len(view_df) - len(prev_df)

                col1, col2 = st.columns([1, 2])
                with col1:
                    st.metric("총 VOC 건수", f"{len(view_df)} 건", f"{delta} 건 (이전 동기간 대비)")
                with col2:
                    st.plotly_chart(create_donut_chart(view_df, "주요 L2 카테고리 TOP 5"), use_container_width=True)

            st.markdown("---")
            
            query_params = st.query_params
            
            if "active_tab" not in st.session_state:
                st.session_state.active_tab = "main"
            
            if query_params.get("tab") == "search":
                st.session_state.active_tab = "search"
                st.query_params.clear()

            tabs = ["📊 카테고리 분석", "🔍 키워드 검색", "💳 결제/인증 리포트"]
            if is_admin:
                tabs.append("🛡️ 어드민 멤버 관리")
            
            # 탭 순서를 고정
            tab_main, tab_search, tab_payment, *tab_admin_list = st.tabs(tabs)

            with tab_main:
                c1, c2 = st.columns(2)
                with c1:
                    st.plotly_chart(create_trend_chart(view_df, date_range, "일자별 VOC 발생 추이"), use_container_width=True)
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
                        disp["문의내용_요약"] = disp["문의내용_요약"].apply(mask_phone_number)
                        show_df = disp.rename(columns={'플랫폼': '구분', '문의내용_요약': '문의 내용'})
                        st.download_button(
                            "📥 CSV 다운로드",
                            data=disp.to_csv(index=False).encode("utf-8-sig"),
                            file_name=f"voc_category_{datetime.now(KST).strftime('%Y%m%d')}.csv",
                            mime="text/csv"
                        )
                        st.dataframe(show_df[["구분","날짜","게임","L1 태그","L2 태그","상담제목","문의 내용","GSN(USN)","기기정보","감성"]],
                                     use_container_width=True, height=500)

            with tab_search:
                st.header("🔍 키워드 검색")
                
                with st.form(key="search_form"):
                    c1, c2 = st.columns([5,1])
                    with c1:
                        keyword = st.text_input("검색 키워드:", value=st.session_state.get("last_search_keyword", ""), placeholder="예: 환불, 튕김, 업데이트...")
                    with c2:
                        st.write(""); st.write("")
                        submitted = st.form_submit_button("검색", use_container_width=True)
                
                st.caption("여러 키워드는 콤마(,)로 구분하여 검색할 수 있습니다. (예: 환불,결제 → '환불' 또는 '결제'가 포함된 항목 검색)")

                if submitted:
                    st.session_state.last_search_keyword = keyword
                    st.session_state.active_tab = "search"
                    st.query_params["tab"] = "search"
                    st.rerun() 

                last_keyword = st.session_state.get("last_search_keyword", "")
                
                # [수정] 탭 활성화 로직 변경
                if st.session_state.active_tab == "search" and last_keyword:
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
                                st.plotly_chart(create_trend_chart(r, date_range, f"'{last_keyword}' 일자별 발생 추이"),
                                                use_container_width=True)
                            with st.container(border=True):
                                st.header("관련 VOC 목록")
                                st.download_button(
                                    "📥 검색 결과 다운로드",
                                    data=r.to_csv(index=False).encode("utf-8-sig"),
                                    file_name=f"voc_search_{last_keyword}_{datetime.now(KST).strftime('%Y%m%d')}.csv",
                                    mime="text/csv"
                                )
                                disp_r = r.rename(columns={'플랫폼':'구분','문의내용_요약':'문의 내용'})
                                st.dataframe(disp_r[["구분","날짜","게임","L2 태그","상담제목","문의 내용","GSN(USN)","기기정보","감성"]],
                                             use_container_width=True, height=400)
                            with st.container(border=True):
                                st.header("연관 키워드 워드클라우드")
                                generate_wordcloud(r["문의내용"])
            
            with tab_payment:
                st.header("💳 결제/인증 리포트")
                st.info("이 탭은 '계정'(로그인/인증) 및 '재화/결제'와 관련된 VOC만 필터링하여 보여줍니다.")
                
                payment_auth_df = view_df[view_df['L1 태그'].isin(['계정', '재화/결제'])].copy()
                
                if payment_auth_df.empty:
                    st.warning("해당 기간에 결제 또는 인증 관련 VOC가 없습니다.")
                else:
                    c1, c2 = st.columns(2)
                    with c1:
                        st.plotly_chart(create_trend_chart(payment_auth_df, date_range, "결제/인증 관련 VOC 발생 추이"), use_container_width=True)
                    with c2:
                        l2_counts_payment = payment_auth_df["L2 태그"].value_counts().nlargest(10).sort_values(ascending=True)
                        fig_l2_payment = px.bar(l2_counts_payment, x=l2_counts_payment.values, y=l2_counts_payment.index, orientation='h',
                                        title="<b>결제/인증 태그 현황 TOP 10</b>", labels={'x': '건수', 'y': '태그'}, text_auto=True)
                        fig_l2_payment.update_layout(height=300)
                        st.plotly_chart(fig_l2_payment, use_container_width=True)
                    
                    with st.container(border=True):
                        st.header("📑 관련 VOC 원본 데이터")
                        disp_payment = payment_auth_df.rename(columns={'플랫폼': '구분', '문의내용_요약': '문의 내용'})
                        st.dataframe(disp_payment[["구분","날짜","게임","L1 태그","L2 태그","상담제목","문의 내용","GSN(USN)","기기정보","감성"]],
                                             use_container_width=True, height=500)
    
    # [수정] 탭이 생성된(데이터가 있는) 경우에만 어드민 탭 로직 실행
    if is_admin and tab_admin_list:
        with tab_admin_list[0]:
            st.subheader("🛡️ 어드민 멤버 관리")
            users_df_latest = fetch_users_table(spreadsheet_id) # 최신 정보로 다시 로드
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

