# ... (기존 코드 생략) ...

# =============================
# 6) MAIN
# =============================
def main():
    # 6-1) 로그인 및 사용자 컨텍스트
    require_login()
# ... (기존 코드 생략) ...
    # 6-8) 날짜 필터 최종 적용
    if filtered.empty or not isinstance(date_range, (list, tuple)) or len(date_range) != 2:
        st.warning("표시할 데이터가 없습니다. 필터/기간을 조정하세요.")
    else:
        start_dt = pd.to_datetime(date_range[0])
# ... (기존 코드 생략) ...
                with col2:
                    st.plotly_chart(create_donut_chart(view_df, "주요 카테고리 TOP 5"), use_container_width=True)

            st.markdown("---")
            
            # [수정 3] 탭 전환 문제 해결: 탭 순서 고정 및 탭 전환 시 rerun 제거
            
            # 탭 생성 (순서 고정)
            tab_main, tab_search = st.tabs(["📊 카테고리 분석", "🔍 키워드 검색"])

            with tab_main:
                # 탭을 클릭해도 세션 상태를 변경하거나 rerun하지 않음
                
                c1, c2 = st.columns(2)
# ... (기존 코드 생략) ...
                        st.dataframe(show_df[["구분","날짜","게임","L2 태그","상담제목","문의 내용","GSN(USN)","기기정보"]],
                                     use_container_width=True, height=500)

            with tab_search:
                # 탭을 클릭해도 세션 상태를 변경하거나 rerun하지 않음

                st.header("🔍 키워드 검색")
                
                with st.form(key="search_form"):
                    c1, c2 = st.columns([5,1])
                    with c1:
                        # 폼 내부의 text_input은 폼 제출 전까지 세션 상태를 업데이트하지 않음
                        keyword = st.text_input("검색 키워드:", value=st.session_state.get("last_search_keyword", ""), placeholder="예: 환불, 튕김, 업데이트...")
                    with c2:
                        st.write(""); st.write("")
                        submitted = st.form_submit_button("검색", use_container_width=True)
                
                # [수정 4] 다중 키워드 검색 안내
                st.caption("여러 키워드는 콤마(,)로 구분하여 검색할 수 있습니다. (예: 환불,결제 → '환불' 또는 '결제'가 포함된 항목 검색)")

                if submitted:
                    # 폼이 제출되면 세션 상태를 업데이트하고 rerun
                    st.session_state.last_search_keyword = keyword
                    st.rerun() 

                last_keyword = st.session_state.get("last_search_keyword", "")
                
                # 폼 제출로 rerun이 발생한 후, last_keyword가 세션에 있으므로 검색 결과 표시
                if last_keyword:
                    keywords = [re.escape(k.strip()) for k in last_keyword.split(",") if k.strip()]
# ... (기존 코드 생략) ...

