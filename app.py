from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st


ROOT = Path(__file__).resolve().parent
EVENTS_PATH = ROOT / "data" / "processed" / "events_asof_2015-09-15_21-00_UTC_with_categories.csv"
FUNNEL_SUMMARY_PATH = ROOT / "data" / "processed" / "funnel_24h_summary.csv"
FUNNEL_DISTRIBUTION_PATH = ROOT / "data" / "processed" / "funnel_24h_transition_distribution.csv"
FUNNEL_CATEGORY_ROOT_PATH = ROOT / "data" / "processed" / "funnel_category_root_map.csv"
RETENTION_SUMMARY_PATH = ROOT / "data" / "processed" / "retention_30d_summary.csv"
RETENTION_CURVES_PATH = ROOT / "data" / "processed" / "retention_30d_curves.csv"
FIRST_VIEW_RETENTION_PATH = ROOT / "data" / "processed" / "first_view_revisit_30d.csv"

st.set_page_config(page_title="구매 전환 지표 대시보드", page_icon="📈", layout="wide")


@st.cache_data(show_spinner="분석 데이터를 불러오는 중입니다…")
def load_events(path: str) -> pd.DataFrame:
    events = pd.read_csv(
        path,
        usecols=["timestamp", "visitorid", "event", "transactionid", "itemid", "categoryid", "root_categoryid"],
        dtype={
            "visitorid": "int64",
            "event": "category",
            "transactionid": "string",
            "itemid": "Int64",
            "categoryid": "Int64",
            "root_categoryid": "Int64",
        },
    )
    events["event_date"] = pd.to_datetime(events["timestamp"], unit="ms", utc=True).dt.floor("D")
    return events


@st.cache_data(show_spinner="일별 리텐션 코호트를 계산하는 중입니다…")
def load_retention_cohorts(path: str) -> pd.DataFrame:
    raw = pd.read_csv(path, usecols=["timestamp", "visitorid"], dtype={"timestamp": "int64", "visitorid": "int64"})
    raw["event_date"] = pd.to_datetime(raw["timestamp"], unit="ms", utc=True).dt.floor("D")
    first_visit = raw.groupby("visitorid", sort=False)["event_date"].min().rename("cohort_date")
    activity = raw[["visitorid", "event_date"]].drop_duplicates()
    activity = activity.join(first_visit, on="visitorid")

    def build_grain(grain: str) -> pd.DataFrame:
        grouped = activity[["visitorid", "event_date", "cohort_date"]].copy()
        if grain == "일별":
            grouped["activity_period"] = grouped["event_date"]
            grouped["cohort_period"] = grouped["cohort_date"]
            grouped["offset"] = (grouped["activity_period"] - grouped["cohort_period"]).dt.days
        elif grain == "주별":
            grouped["activity_period"] = grouped["event_date"] - pd.to_timedelta(grouped["event_date"].dt.weekday, unit="D")
            grouped["cohort_period"] = grouped["cohort_date"] - pd.to_timedelta(grouped["cohort_date"].dt.weekday, unit="D")
            grouped["offset"] = ((grouped["activity_period"] - grouped["cohort_period"]).dt.days // 7).astype("int16")
        else:
            grouped["activity_period"] = grouped["event_date"].dt.to_period("M").dt.to_timestamp().dt.tz_localize("UTC")
            grouped["cohort_period"] = grouped["cohort_date"].dt.to_period("M").dt.to_timestamp().dt.tz_localize("UTC")
            grouped["offset"] = (
                (grouped["activity_period"].dt.year - grouped["cohort_period"].dt.year) * 12
                + grouped["activity_period"].dt.month - grouped["cohort_period"].dt.month
            ).astype("int16")
        max_offset = {"일별": 30, "주별": 12, "월별": 6}[grain]
        grouped = grouped.loc[grouped["offset"].between(0, max_offset)].drop_duplicates(
            ["visitorid", "cohort_period", "activity_period"]
        )
        counts = grouped.groupby(["cohort_date", "cohort_period", "offset"], observed=True)["visitorid"].nunique().rename("returning_users").reset_index()
        cohorts = (
            grouped.loc[grouped["offset"] == 0]
            .groupby(["cohort_date", "cohort_period"], observed=True)["visitorid"]
            .nunique().rename("cohort_users").reset_index()
        )
        result = counts.merge(cohorts, on=["cohort_date", "cohort_period"], how="left")
        result["retention_rate"] = result["returning_users"] / result["cohort_users"]
        result["grain"] = grain
        return result

    return pd.concat([build_grain(grain) for grain in ["일별", "주별", "월별"]], ignore_index=True)


@st.cache_data(show_spinner="24시간 퍼널 집계를 불러오는 중입니다…")
def load_funnel_tables(summary_path: str, distribution_path: str, category_root_path: str):
    summary = pd.read_csv(summary_path, dtype={"entity_id": "string"})
    distributions = pd.read_csv(distribution_path, dtype={"entity_id": "string", "time_bucket": "string"})
    category_root = pd.read_csv(category_root_path, dtype={"category_id": "string", "root_category_id": "string"})
    return summary, distributions, category_root


@st.cache_data(show_spinner="30일 리텐션 집계를 불러오는 중입니다…")
def load_retention_tables(summary_path: str, curves_path: str, first_view_path: str):
    summary = pd.read_csv(summary_path, dtype={"entity_id": "string"})
    curves = pd.read_csv(curves_path, dtype={"entity_id": "string", "metric": "string"})
    first_view = pd.read_csv(first_view_path, dtype={"entity_id": "string"})
    return summary, curves, first_view


@st.cache_data
def load_category_root_mapping(path: str) -> pd.DataFrame:
    return pd.read_csv(path, dtype={"category_id": "string", "root_category_id": "string"})


st.title("구매 전환 지표 대시보드")
st.caption("UTC 기준 · 2015-09-15 21:00까지 · 완전 중복 이벤트 제거")

if not EVENTS_PATH.exists():
    st.error("카테고리 결합 이벤트 파일을 찾을 수 없습니다. 먼저 `python analysis/join_categories.py`를 실행해 주세요.")
    st.stop()

events = load_events(str(EVENTS_PATH))
min_date = events["event_date"].min().date()
max_date = events["event_date"].max().date()

selected_dates = st.date_input(
    "분석 기간 (UTC)",
    value=(min_date, max_date),
    min_value=min_date,
    max_value=max_date,
    format="YYYY-MM-DD",
)
if isinstance(selected_dates, (tuple, list)):
    if len(selected_dates) == 2:
        start_date, end_date = selected_dates
    elif len(selected_dates) == 1:
        start_date = end_date = selected_dates[0]
    else:
        st.info("분석할 날짜 범위를 선택해 주세요.")
        st.stop()
else:
    start_date = end_date = selected_dates

start_utc = pd.Timestamp(start_date, tz="UTC")
end_utc = pd.Timestamp(end_date, tz="UTC") + pd.Timedelta(days=1)
selected = events.loc[(events["event_date"] >= start_utc) & (events["event_date"] < end_utc)]
if selected.empty:
    st.warning("선택한 기간에 이벤트가 없습니다.")
    st.stop()

transactions = selected.loc[selected["event"] == "transaction"]
active_users = selected["visitorid"].nunique()
buyers = transactions["visitorid"].nunique()
purchase_count = transactions["transactionid"].nunique(dropna=True)
cart_count = int((selected["event"] == "addtocart").sum())
event_count = len(selected)

overview_tab, category_tab, funnel_tab, retention_tab = st.tabs(["주요 지표", "카테고리별 첫 조회 성과", "24시간 퍼널", "30일 리텐션"])

with overview_tab:
    st.subheader("주요 지표")
    st.caption(f"{start_date:%Y-%m-%d} – {end_date:%Y-%m-%d} UTC")
    metric_columns = st.columns(5)
    metric_columns[0].metric("활성 사용자", f"{active_users:,}", help="선택 기간에 어떤 이벤트든 한 번 이상 수행한 고유 방문자")
    metric_columns[1].metric("구매자", f"{buyers:,}", help="transaction 이벤트를 한 번 이상 수행한 고유 방문자")
    metric_columns[2].metric("구매 횟수", f"{purchase_count:,}", help="고유 transactionid 수")
    metric_columns[3].metric("장바구니 담기", f"{cart_count:,}", help="addtocart 이벤트 행 수")
    metric_columns[4].metric("전체 이벤트", f"{event_count:,}", help="선택 기간의 중복 제거 이벤트 행 수")

    st.divider()
    st.subheader("기간별 추이")
    metric_labels = {
        "활성 사용자": "active_users",
        "구매자": "buyers",
        "구매 횟수": "purchase_count",
        "장바구니 담기": "cart_count",
        "전체 이벤트": "event_count",
    }
    selected_metric = st.selectbox("추이 지표", list(metric_labels))
    column = metric_labels[selected_metric]

    def trend_frame(frame: pd.DataFrame, index: pd.Index, key: str) -> pd.DataFrame:
        tx = frame.loc[frame["event"] == "transaction"]
        trend = pd.DataFrame(index=index)
        trend["active_users"] = frame.groupby(key, observed=True)["visitorid"].nunique().reindex(index, fill_value=0)
        trend["buyers"] = tx.groupby(key, observed=True)["visitorid"].nunique().reindex(index, fill_value=0)
        trend["purchase_count"] = tx.groupby(key, observed=True)["transactionid"].nunique().reindex(index, fill_value=0)
        trend["cart_count"] = frame.loc[frame["event"] == "addtocart"].groupby(key, observed=True).size().reindex(index, fill_value=0)
        trend["event_count"] = frame.groupby(key, observed=True).size().reindex(index, fill_value=0)
        return trend

    daily_index = pd.date_range(start_utc, end_utc - pd.Timedelta(days=1), freq="D", tz="UTC")
    selected_daily = trend_frame(selected, daily_index, "event_date")
    month_key = selected["event_date"].dt.strftime("%Y-%m")
    monthly_data = selected.assign(month=month_key)
    month_index = pd.period_range(start=start_date.strftime("%Y-%m"), end=end_date.strftime("%Y-%m"), freq="M").strftime("%Y-%m")
    selected_monthly = trend_frame(monthly_data, pd.Index(month_index), "month")

    st.markdown("**일별 추이**")
    st.line_chart(
        selected_daily[[column]].rename(columns={column: selected_metric}),
        x_label="날짜 (UTC)",
        y_label=selected_metric,
        height=300,
        use_container_width=True,
    )
    st.markdown("**월별 추이**")
    st.line_chart(
        selected_monthly[[column]].rename(columns={column: selected_metric}),
        x_label="월 (UTC)",
        y_label=selected_metric,
        height=300,
        use_container_width=True,
    )
    st.caption("선택한 날짜 범위 기준으로 집계합니다. 일별·월별 구매 횟수는 각 구간 내 고유 transactionid 수이며, 서로 다른 구간에 같은 거래 ID가 있으면 해당 구간마다 집계될 수 있습니다.")
    st.caption("초기 4주(2015-05-03 03:00:04 UTC부터 28일)는 데이터 시작 전 행동 이력이 관측되지 않는 구간입니다.")

    st.divider()
    st.subheader("카테고리별 전환율 산포도")
    st.caption("선택 기간에 방문자별 첫 조회를 기준으로 카테고리별 장바구니·구매 전환율을 계산합니다. 풍선 크기는 첫 조회 방문자 수이며, 같은 대분류는 같은 색으로 표시합니다.")
    category_first_views = selected.loc[selected["event"] == "view"].sort_values("timestamp", kind="stable").drop_duplicates("visitorid", keep="first")
    if category_first_views.empty:
        st.info("선택 기간에 표시할 첫 조회 데이터가 없습니다.")
    else:
        category_first_views = category_first_views.copy()
        category_first_views["categoryid"] = category_first_views["categoryid"].astype("string").fillna("카테고리 없음")
        category_first_views["root_categoryid"] = category_first_views["root_categoryid"].astype("string").fillna("대분류 없음")
        category_first_views["first_categoryid"] = category_first_views["categoryid"]
        category_first_views["first_root_categoryid"] = category_first_views["root_categoryid"]
        category_events = selected.loc[selected["timestamp"].ge(category_first_views["timestamp"].min())].copy()
        category_events["categoryid"] = category_events["categoryid"].astype("string").fillna("카테고리 없음")
        category_events["root_categoryid"] = category_events["root_categoryid"].astype("string").fillna("대분류 없음")
        category_cohort = category_first_views.set_index("visitorid")
        category_events = category_events.join(
            category_cohort[["timestamp", "categoryid", "root_categoryid"]].rename(
                columns={"timestamp": "first_view_timestamp", "categoryid": "first_categoryid", "root_categoryid": "first_root_categoryid"}
            ),
            on="visitorid",
            how="inner",
            rsuffix="_first",
        )
        same_category = category_events["categoryid"].eq(category_events["first_categoryid"])
        after_first_view = category_events["timestamp"].gt(category_events["first_view_timestamp"])
        cart_by_category = category_events.loc[same_category & after_first_view & category_events["event"].eq("addtocart")].groupby("first_categoryid")["visitorid"].nunique()
        purchase_by_category = category_events.loc[same_category & after_first_view & category_events["event"].eq("transaction")].groupby("first_categoryid")["visitorid"].nunique()
        scatter_data = category_first_views.groupby(["categoryid", "root_categoryid"], observed=True).agg(first_views=("visitorid", "nunique")).reset_index()
        scatter_data["cart_rate"] = scatter_data["categoryid"].map(cart_by_category).fillna(0) / scatter_data["first_views"]
        scatter_data["purchase_rate"] = scatter_data["categoryid"].map(purchase_by_category).fillna(0) / scatter_data["first_views"]
        scatter_data["장바구니 전환율 (%)"] = scatter_data["cart_rate"] * 100
        scatter_data["구매 전환율 (%)"] = scatter_data["purchase_rate"] * 100
        scatter_fig = px.scatter(
            scatter_data,
            x="cart_rate",
            y="purchase_rate",
            size="first_views",
            color="root_categoryid",
            hover_name="categoryid",
            hover_data={
                "root_categoryid": True,
                "first_views": ":,",
                "장바구니 전환율 (%)": ":.2f",
                "구매 전환율 (%)": ":.2f",
                "cart_rate": False,
                "purchase_rate": False,
            },
            size_max=42,
            opacity=0.72,
            template="plotly_dark",
            labels={
                "cart_rate": "장바구니 전환율",
                "purchase_rate": "구매 전환율",
                "first_views": "첫 조회 수",
                "root_categoryid": "대분류",
                "categoryid": "카테고리",
            },
        )
        scatter_fig.update_layout(height=560, legend_title_text="대분류", margin=dict(l=20, r=20, t=25, b=20))
        scatter_fig.update_xaxes(tickformat=".1%")
        scatter_fig.update_yaxes(tickformat=".1%")
        st.plotly_chart(scatter_fig, use_container_width=True)

with category_tab:
    st.subheader("카테고리별 첫 조회 성과")
    st.caption("선택 기간에 방문자별로 가장 먼저 발생한 view 이벤트 한 건을 기준으로 집계합니다. 표기는 카테고리 ID이며, 이름 정보는 원본에 없습니다.")
    st.caption("장바구니·구매 전환은 최초 조회 이후 같은 대분류·카테고리·아이템에서 발생한 고유 방문자 수를 분자로, 해당 항목의 첫 조회 방문자 수를 분모로 계산합니다. '카테고리 없음'은 최초 조회와 후속 이벤트 모두 카테고리 매핑이 없는 경우만 전환으로 셉니다.")
    first_views = selected.loc[selected["event"] == "view"].sort_values("timestamp", kind="stable").drop_duplicates("visitorid", keep="first")
    if first_views.empty:
        st.info("선택 기간에 조회(view) 이벤트가 없습니다.")
    else:
        total_first_viewers = len(first_views)
        if FIRST_VIEW_RETENTION_PATH.exists():
            _, _, first_view_retention = load_retention_tables(
                str(RETENTION_SUMMARY_PATH), str(RETENTION_CURVES_PATH), str(FIRST_VIEW_RETENTION_PATH)
            )
        else:
            first_view_retention = pd.DataFrame(columns=["scope", "entity_id", "eligible_first_view_users", "revisiting_users_30d", "first_view_revisit_rate_30d"])
        first_view_overall_retention = first_view_retention.loc[
            (first_view_retention["scope"] == "overall") & (first_view_retention["entity_id"] == "전체")
        ]

        cohort = first_views[["visitorid", "timestamp", "root_categoryid", "categoryid", "itemid"]].rename(
            columns={
                "timestamp": "first_view_timestamp",
                "root_categoryid": "first_root_categoryid",
                "categoryid": "first_categoryid",
                "itemid": "first_itemid",
            }
        )
        later_events = (
            selected.loc[
                selected["event"].isin(["addtocart", "transaction"]),
                ["visitorid", "timestamp", "event", "root_categoryid", "categoryid", "itemid"],
            ]
            .merge(cohort, on="visitorid", how="inner", validate="many_to_one")
        )
        later_events = later_events.loc[later_events["timestamp"] > later_events["first_view_timestamp"]]
        overall_cart_visitors = later_events.loc[later_events["event"] == "addtocart", "visitorid"].nunique()
        overall_purchase_visitors = later_events.loc[later_events["event"] == "transaction", "visitorid"].nunique()

        st.markdown(f"### 주요 성과 · {start_date:%Y-%m-%d} ~ {end_date:%Y-%m-%d}")
        summary_cols = st.columns(4)
        summary_cols[0].metric("첫 조회 방문자", f"{total_first_viewers:,}명", help="선택 기간에 view를 한 번 이상 수행한 고유 방문자")
        summary_cols[1].metric(
            "장바구니 전환율",
            f"{overall_cart_visitors / total_first_viewers:.2%}",
            help=f"최초 조회 이후 장바구니를 이용한 방문자 {overall_cart_visitors:,}명 ÷ 첫 조회 방문자",
        )
        summary_cols[2].metric(
            "구매 전환율",
            f"{overall_purchase_visitors / total_first_viewers:.2%}",
            help=f"최초 조회 이후 구매한 방문자 {overall_purchase_visitors:,}명 ÷ 첫 조회 방문자",
        )
        if not first_view_overall_retention.empty:
            retention_row = first_view_overall_retention.iloc[0]
            summary_cols[3].metric(
                "30일 재방문율",
                f"{retention_row['first_view_revisit_rate_30d']:.2%}",
                help=f"30일 완전 관찰 가능한 첫 조회 코호트 {int(retention_row['eligible_first_view_users']):,}명 기준; 상단 날짜 선택과 무관한 전체 관찰 코호트",
            )
        st.caption("30일 재방문율은 기준 시각까지 30일 관찰이 가능한 전체 첫 조회 코호트 기준입니다. 표의 첫 조회·전환 수치는 상단에서 고른 기간을 따르며, 재방문율 분모는 표의 '30일 코호트' 열에 표시합니다.")

        def performance_table(
            frame: pd.DataFrame,
            key: str,
            label: str,
            limit: int | None = 10,
        ) -> pd.DataFrame:
            first_key = f"first_{key}"
            group = frame.groupby(key, observed=True, dropna=False)["visitorid"].nunique()
            outcome_rows = later_events.loc[later_events["visitorid"].isin(frame["visitorid"])]
            same_entity = outcome_rows[key].eq(outcome_rows[first_key]).fillna(False) | (
                outcome_rows[key].isna() & outcome_rows[first_key].isna()
            )
            cart_visitors = (
                outcome_rows.loc[same_entity & (outcome_rows["event"] == "addtocart")]
                .groupby(first_key, observed=True)["visitorid"]
                .nunique()
            )
            buyer_visitors = (
                outcome_rows.loc[same_entity & (outcome_rows["event"] == "transaction")]
                .groupby(first_key, observed=True)["visitorid"]
                .nunique()
            )
            result = group.rename("첫 조회 방문자 수").to_frame()
            result["장바구니 전환 방문자 수"] = cart_visitors.reindex(result.index, fill_value=0)
            result["구매 전환 방문자 수"] = buyer_visitors.reindex(result.index, fill_value=0)
            result["비중 (%)"] = (result["첫 조회 방문자 수"] / total_first_viewers * 100).round(2)
            result["장바구니 전환율 (%)"] = (result["장바구니 전환 방문자 수"] / result["첫 조회 방문자 수"] * 100).round(2)
            result["구매 전환율 (%)"] = (result["구매 전환 방문자 수"] / result["첫 조회 방문자 수"] * 100).round(2)
            result = result.sort_values("첫 조회 방문자 수", ascending=False)
            if limit is not None:
                result = result.head(limit)
            result = result.reset_index().rename(columns={key: label})
            missing_label = "대분류 없음" if key == "root_categoryid" else "카테고리 없음" if key == "categoryid" else "아이템 없음"
            result[label] = result[label].astype("string").fillna(missing_label)
            retention_scope = {"root_categoryid": "root", "categoryid": "category", "itemid": "item"}[key]
            retention_rows = first_view_retention.loc[first_view_retention["scope"] == retention_scope].set_index("entity_id")
            result["30일 코호트"] = result[label].map(retention_rows["eligible_first_view_users"]).fillna(0).astype("int64")
            result["30일 재방문자"] = result[label].map(retention_rows["revisiting_users_30d"]).fillna(0).astype("int64")
            result["30일 재방문율 (%)"] = result[label].map(retention_rows["first_view_revisit_rate_30d"])
            result.insert(0, "순위", range(1, len(result) + 1))
            return result

        chart_metric = st.selectbox(
            "막대그래프 지표",
            ["첫 조회 방문자 수", "장바구니 전환율 (%)", "구매 전환율 (%)", "30일 재방문율 (%)"],
        )

        def show_rank(level_title: str, label: str, table: pd.DataFrame) -> None:
            st.markdown(f"**{level_title} 기준 첫 조회 상위 10**")
            if table.empty:
                st.info("표시할 항목이 없습니다.")
                return
            plot_data = table.sort_values(chart_metric, ascending=True)
            fig = px.bar(
                plot_data,
                x=chart_metric,
                y=label,
                orientation="h",
                text=chart_metric,
                template="plotly_dark",
                color_discrete_sequence=["#ff4b4b"],
            )
            fig.update_traces(textposition="outside", cliponaxis=False, hovertemplate=f"%{{y}}<br>{chart_metric}: %{{x:,.2f}}<extra></extra>")
            fig.update_layout(
                height=390,
                margin=dict(l=10, r=60, t=15, b=35),
                showlegend=False,
                xaxis_title=chart_metric,
                yaxis_title="",
                yaxis=dict(categoryorder="array", categoryarray=plot_data[label].tolist()),
                coloraxis_showscale=False,
            )
            st.plotly_chart(fig, use_container_width=True)
            st.dataframe(table, hide_index=True, use_container_width=True)

        st.markdown("**주요 성과 · 상위 10개**")
        show_rank("대분류", "대분류 ID", performance_table(first_views, "root_categoryid", "대분류 ID"))
        show_rank("카테고리", "카테고리 ID", performance_table(first_views, "categoryid", "카테고리 ID"))
        show_rank("아이템", "아이템 ID", performance_table(first_views, "itemid", "아이템 ID"))

        st.divider()
        st.markdown("**계층별 상세 조회**")
        root_ids = sorted(first_views["root_categoryid"].dropna().astype(int).unique().tolist())
        root_choice = st.selectbox("대분류", ["전체 대분류"] + [str(value) for value in root_ids])
        root_subset = first_views if root_choice == "전체 대분류" else first_views.loc[first_views["root_categoryid"] == int(root_choice)]
        category_ids = sorted(root_subset["categoryid"].dropna().astype(int).unique().tolist())
        category_choice = st.selectbox(
            "세부 카테고리",
            ["전체 카테고리"] + [str(value) for value in category_ids],
            key=f"category_choice_{root_choice}",
        )
        if category_choice == "전체 카테고리":
            st.info("대분류를 선택하면 그 안의 카테고리별 성과가 표시됩니다. 세부 카테고리를 고르면 아이템별로 좁혀 볼 수 있습니다.")
            if root_choice != "전체 대분류":
                detail = performance_table(root_subset, "categoryid", "카테고리 ID", limit=20)
                st.dataframe(detail, hide_index=True, use_container_width=True)
        else:
            category_subset = root_subset.loc[root_subset["categoryid"] == int(category_choice)]
            item_ids = sorted(category_subset["itemid"].dropna().astype(int).unique().tolist())
            item_choice = st.selectbox(
                "세부 아이템",
                ["전체 아이템"] + [str(value) for value in item_ids],
                key=f"item_choice_{root_choice}_{category_choice}",
            )
            if item_choice == "전체 아이템":
                item_detail = performance_table(category_subset, "itemid", "아이템 ID", limit=20)
                st.dataframe(item_detail, hide_index=True, use_container_width=True)
            else:
                selected_item = category_subset.loc[category_subset["itemid"] == int(item_choice)]
                item_metrics = performance_table(selected_item, "itemid", "아이템 ID", limit=None)
                if not item_metrics.empty:
                    item_metrics = item_metrics.iloc[0]
                    metric_cols = st.columns(5)
                    metric_cols[0].metric("첫 조회 방문자", f"{int(item_metrics['첫 조회 방문자 수']):,}")
                    metric_cols[1].metric("장바구니 전환 방문자", f"{int(item_metrics['장바구니 전환 방문자 수']):,}")
                    metric_cols[2].metric("장바구니 전환율", f"{item_metrics['장바구니 전환율 (%)']:.2f}%")
                    metric_cols[3].metric("구매 전환 방문자", f"{int(item_metrics['구매 전환 방문자 수']):,}")
                    metric_cols[4].metric("구매 전환율", f"{item_metrics['구매 전환율 (%)']:.2f}%")

with funnel_tab:
    st.subheader("24시간 구매 퍼널")
    if not all(path.exists() for path in [FUNNEL_SUMMARY_PATH, FUNNEL_DISTRIBUTION_PATH, FUNNEL_CATEGORY_ROOT_PATH]):
        st.error("24시간 퍼널 집계 파일을 찾을 수 없습니다. `python analysis/funnel_24h.py`를 실행해 주세요.")
    else:
        funnel_summary, funnel_distributions, category_root_map = load_funnel_tables(
            str(FUNNEL_SUMMARY_PATH), str(FUNNEL_DISTRIBUTION_PATH), str(FUNNEL_CATEGORY_ROOT_PATH)
        )
        st.caption("24시간 전환 창은 각 퍼널 진입 View 시점부터 계산합니다. 마지막 24시간은 완전한 후속 관찰이 불가능해 View 기준 코호트에서 제외했습니다. 단계 전환은 timestamp가 엄격히 증가하는 경우만 인정합니다.")
        st.caption("이 탭은 2015-05-03부터 2015-09-14 21:00 UTC까지 진입한 전체 유효 코호트 기준입니다. 상단 날짜 선택은 주요 지표와 첫 조회 성과 탭에 적용되고, 24시간 퍼널 집계는 전체 기간으로 고정됩니다.")
        st.caption("대분류·카테고리 퍼널은 방문자별 해당 그룹의 첫 조회를 시작점으로 하고, 이후 같은 그룹에서 발생한 행동만 연결합니다. 한 방문자는 여러 그룹 퍼널에 포함될 수 있습니다.")

        overall_row = funnel_summary.loc[funnel_summary["scope"] == "overall"].iloc[0]

        def funnel_details(row: pd.Series) -> pd.DataFrame:
            view_users = int(row["view_users"])
            cart_users = int(row["cart_users"])
            purchase_users = int(row["purchase_users"])
            cart_rate = float(row["view_to_cart_rate"])
            purchase_after_cart = row["cart_to_purchase_rate"]
            purchase_after_cart = float(purchase_after_cart) if pd.notna(purchase_after_cart) else None
            return pd.DataFrame(
                [
                    {"단계": "View", "도달 방문자": view_users, "이전 단계 방문자": "—", "전환율": "—", "이벤트 횟수": int(row["view_events"])},
                    {"단계": "AddToCart", "도달 방문자": cart_users, "이전 단계 방문자": view_users, "전환율": f"{cart_rate:.2%}", "이벤트 횟수": int(row["cart_events"])},
                    {"단계": "Transaction", "도달 방문자": purchase_users, "이전 단계 방문자": cart_users, "전환율": f"{purchase_after_cart:.2%}" if purchase_after_cart is not None else "—", "이벤트 횟수": int(row["transaction_events"])},
                ]
            )

        def funnel_figure(row: pd.Series, title: str) -> go.Figure:
            view_users = int(row["view_users"])
            cart_users = int(row["cart_users"])
            purchase_users = int(row["purchase_users"])
            cart_rate = float(row["view_to_cart_rate"])
            cart_purchase_rate = row["cart_to_purchase_rate"]
            cart_purchase_rate = float(cart_purchase_rate) if pd.notna(cart_purchase_rate) else 0.0
            labels = ["View", "AddToCart", "Transaction"]
            values = [view_users, cart_users, purchase_users]
            previous_users = [0, view_users, cart_users]
            rates = [0.0, cart_rate, cart_purchase_rate]
            event_counts = [int(row["view_events"]), int(row["cart_events"]), int(row["transaction_events"])]
            trace = go.Funnel(
                y=labels,
                x=values,
                textinfo="value+percent previous",
                textposition="outside",
                customdata=list(zip(previous_users, rates, event_counts)),
                marker={"color": ["#4C8BF5", "#F3A43B", "#49A078"]},
                hovertemplate="%{y}<br>도달 방문자: %{x:,.0f}<br>이전 단계 방문자: %{customdata[0]:,.0f}<br>이전 단계 대비 전환율: %{customdata[1]:.2%}<br>이벤트 횟수: %{customdata[2]:,.0f}<extra></extra>",
            )
            fig = go.Figure(trace)
            fig.update_layout(
                template="plotly_dark",
                title=title,
                height=390,
                margin=dict(l=30, r=110, t=55, b=25),
                showlegend=False,
            )
            return fig

        st.markdown("**전체 퍼널**")
        st.plotly_chart(funnel_figure(overall_row, "View → AddToCart → Transaction"), use_container_width=True)
        st.dataframe(funnel_details(overall_row), hide_index=True, use_container_width=True)
        st.caption("‘이전 단계 방문자’가 각 전환율의 분모입니다. 이벤트 횟수는 반복 행동과 transaction의 상품별 행을 포함하므로 도달 방문자 수보다 클 수 있습니다.")

        root_summary = funnel_summary.loc[funnel_summary["scope"] == "root"].copy()
        category_summary = funnel_summary.loc[funnel_summary["scope"] == "category"].copy()
        mapped_roots = root_summary.loc[root_summary["entity_id"] != "대분류 없음"].sort_values("view_users", ascending=False)
        root_options = mapped_roots["entity_id"].tolist() + (["대분류 없음"] if (root_summary["entity_id"] == "대분류 없음").any() else [])
        if root_options:
            default_root = mapped_roots.iloc[0]["entity_id"] if not mapped_roots.empty else root_options[0]
            root_index = root_options.index(default_root)
            root_choice = st.selectbox("대분류 퍼널", root_options, index=root_index, key="funnel_root_choice")
            selected_root_row = root_summary.loc[root_summary["entity_id"] == root_choice].iloc[0]

            if root_choice == "대분류 없음":
                category_options = ["카테고리 없음"] if (category_summary["entity_id"] == "카테고리 없음").any() else []
            else:
                category_ids = category_root_map.loc[category_root_map["root_category_id"] == root_choice, "category_id"].tolist()
                category_options = category_summary.loc[category_summary["entity_id"].isin(category_ids)].sort_values("view_users", ascending=False)["entity_id"].tolist()

            if category_options:
                category_choice = st.selectbox("세부 카테고리 퍼널", category_options, key=f"funnel_category_choice_{root_choice}")
                selected_category_row = category_summary.loc[category_summary["entity_id"] == category_choice].iloc[0]
            else:
                category_choice = None
                selected_category_row = None
                st.info("선택한 대분류 아래에 조회 가능한 카테고리 퍼널이 없습니다.")

            root_chart_col, category_chart_col = st.columns(2)
            with root_chart_col:
                st.plotly_chart(funnel_figure(selected_root_row, f"대분류 {root_choice} 퍼널"), use_container_width=True)
                st.dataframe(funnel_details(selected_root_row), hide_index=True, use_container_width=True)
            with category_chart_col:
                if selected_category_row is not None:
                    st.plotly_chart(funnel_figure(selected_category_row, f"카테고리 {category_choice} 퍼널"), use_container_width=True)
                    st.dataframe(funnel_details(selected_category_row), hide_index=True, use_container_width=True)

            st.markdown("**전환 시간 분포**")
            distribution_target = st.selectbox(
                "분포 기준",
                ["전체 퍼널", "선택한 대분류", "선택한 카테고리"],
                key="funnel_distribution_target",
            )
            if distribution_target == "전체 퍼널":
                dist_scope, dist_entity = "overall", "전체"
            elif distribution_target == "선택한 대분류":
                dist_scope, dist_entity = "root", root_choice
            elif selected_category_row is not None:
                dist_scope, dist_entity = "category", category_choice
            else:
                dist_scope, dist_entity = "root", root_choice
            chosen_distribution = funnel_distributions.loc[
                (funnel_distributions["scope"] == dist_scope)
                & (funnel_distributions["entity_id"].fillna("") == dist_entity)
            ]
            bucket_order = ["0–1분", "1–5분", "5–15분", "15–60분", "1–3시간", "3–6시간", "6–12시간", "12–24시간"]
            time_cols = st.columns(2)
            for col, transition in zip(time_cols, ["View→AddToCart", "AddToCart→Transaction"]):
                distribution = chosen_distribution.loc[chosen_distribution["transition"] == transition].copy()
                distribution["time_bucket"] = pd.Categorical(distribution["time_bucket"], categories=bucket_order, ordered=True)
                distribution = distribution.sort_values("time_bucket")
                with col:
                    st.markdown(f"**{transition} 소요 시간**")
                    if distribution.empty:
                        st.info("전환된 방문자가 없습니다.")
                    else:
                        time_fig = px.bar(
                            distribution,
                            x="time_bucket",
                            y="visitors",
                            category_orders={"time_bucket": bucket_order},
                            labels={"time_bucket": "소요 시간", "visitors": "전환 방문자 수"},
                            template="plotly_dark",
                            color_discrete_sequence=["#F3A43B" if transition == "View→AddToCart" else "#49A078"],
                        )
                        time_fig.update_layout(height=320, margin=dict(l=10, r=10, t=15, b=45), showlegend=False)
                        st.plotly_chart(time_fig, use_container_width=True)

        st.markdown("**구매 전환율 상위 그룹 비교 (첫 조회 1,000명 이상)**")
        root_compare = root_summary.loc[(root_summary["entity_id"] != "대분류 없음") & (root_summary["view_users"] >= 1000)].nlargest(10, "view_to_purchase_rate").sort_values("view_to_purchase_rate")
        category_compare = category_summary.loc[(category_summary["entity_id"] != "카테고리 없음") & (category_summary["view_users"] >= 1000)].nlargest(10, "view_to_purchase_rate").sort_values("view_to_purchase_rate")
        compare_root_col, compare_category_col = st.columns(2)
        for col, data, name in [(compare_root_col, root_compare, "대분류"), (compare_category_col, category_compare, "카테고리")]:
            with col:
                st.markdown(f"**{name}별 View→Transaction 전환율**")
                compare_fig = px.bar(
                    data,
                    x="view_to_purchase_rate",
                    y="entity_id",
                    orientation="h",
                    text=data["view_to_purchase_rate"].map(lambda value: f"{value:.2%}"),
                    labels={"entity_id": f"{name} ID", "view_to_purchase_rate": "구매 전환율"},
                    template="plotly_dark",
                    color_discrete_sequence=["#ff4b4b"],
                )
                compare_fig.update_traces(textposition="outside", cliponaxis=False, hovertemplate=f"{name}: %{{y}}<br>구매 전환율: %{{x:.2%}}<br>첫 조회 방문자: %{{customdata[0]:,}}<extra></extra>", customdata=data[["view_users"]].to_numpy())
                compare_fig.update_layout(height=370, margin=dict(l=10, r=55, t=15, b=35), showlegend=False, xaxis_tickformat=".0%")
                st.plotly_chart(compare_fig, use_container_width=True)

with retention_tab:
    st.subheader("30일 리텐션")
    all_retention_cohorts = load_retention_cohorts(str(EVENTS_PATH))
    cohort_visit_dates = sorted(all_retention_cohorts["cohort_date"].drop_duplicates())
    if cohort_visit_dates:
        latest_visit_date = cohort_visit_dates[-1]
        default_visit_start = max(cohort_visit_dates[0], latest_visit_date - pd.Timedelta(days=29))
        retention_visit_dates = st.date_input(
            "코호트 사용자 첫 방문일 범위 (UTC)",
            value=(default_visit_start.date(), latest_visit_date.date()),
            min_value=cohort_visit_dates[0].date(),
            max_value=latest_visit_date.date(),
            key="retention_cohort_visit_dates",
        )
        if isinstance(retention_visit_dates, (tuple, list)):
            if len(retention_visit_dates) == 2:
                retention_visit_start, retention_visit_end = retention_visit_dates
            elif len(retention_visit_dates) == 1:
                retention_visit_start = retention_visit_end = retention_visit_dates[0]
            else:
                retention_visit_start, retention_visit_end = default_visit_start.date(), latest_visit_date.date()
        else:
            retention_visit_start = retention_visit_end = retention_visit_dates
        retention_visit_start_utc = pd.Timestamp(retention_visit_start, tz="UTC")
        retention_visit_end_utc = pd.Timestamp(retention_visit_end, tz="UTC")
        st.caption("이 날짜 범위는 아래 코호트 히트맵에 적용됩니다. 선택 범위 안에 첫 방문한 사용자만 포함하며, 주별·월별 보기도 먼저 방문일 범위를 적용한 뒤 코호트 기간으로 묶습니다.")
    if not all(path.exists() for path in [RETENTION_SUMMARY_PATH, RETENTION_CURVES_PATH, FIRST_VIEW_RETENTION_PATH, FUNNEL_CATEGORY_ROOT_PATH]):
        st.error("30일 리텐션 집계 파일을 찾을 수 없습니다. `python analysis/retention_30d.py`를 실행해 주세요.")
    else:
        retention_summary, retention_curves, _ = load_retention_tables(
            str(RETENTION_SUMMARY_PATH), str(RETENTION_CURVES_PATH), str(FIRST_VIEW_RETENTION_PATH)
        )
        category_root_map = load_category_root_mapping(str(FUNNEL_CATEGORY_ROOT_PATH))
        overall_retention = retention_summary.loc[
            (retention_summary["scope"] == "overall") & (retention_summary["entity_id"] == "전체")
        ].iloc[0]
        st.caption("상단 날짜 선택과 무관하게 전체 관측기간에서 30일 후속 관찰이 가능한 코호트를 사용합니다. 코호트 진입일은 2015-08-16 21:00 UTC까지이며, 누적 리텐션 곡선의 30일 값이 각 30일 재방문율입니다.")
        st.caption("재방문율은 첫 이벤트 이후 모든 이벤트 재발생, 재구매율은 첫 구매 후 다른 transactionid의 구매, 구매자 재방문율은 첫 구매 후 다른 거래 행을 제외한 모든 이벤트 재발생으로 계산합니다.")

        overall_metrics = st.columns(3)
        overall_metrics[0].metric(
            "30일 재방문율",
            f"{overall_retention['all_event_revisit_rate_30d']:.2%}",
            help=f"전체 이벤트 첫 방문 {int(overall_retention['all_event_cohort_users']):,}명 중 {int(overall_retention['all_event_revisitors_30d']):,}명이 30일 내 재방문",
        )
        overall_metrics[1].metric(
            "30일 재구매율",
            f"{overall_retention['repurchase_rate_30d']:.2%}",
            help=f"첫 구매 코호트 {int(overall_retention['purchase_cohort_users']):,}명 중 {int(overall_retention['repeat_purchasers_30d']):,}명이 다른 거래로 재구매",
        )
        overall_metrics[2].metric(
            "구매자 30일 재방문율",
            f"{overall_retention['purchaser_revisit_rate_30d']:.2%}",
            help=f"첫 구매 코호트 {int(overall_retention['purchase_cohort_users']):,}명 중 {int(overall_retention['purchaser_revisitors_30d']):,}명이 어떤 이벤트로든 재방문",
        )

        metric_names = {
            "all_event_revisit": "전체 이벤트 재방문",
            "repurchase": "재구매",
            "purchaser_revisit": "구매자 재방문",
        }

        def retention_figure(scope: str, entity_id: str, title: str) -> go.Figure:
            data = retention_curves.loc[
                (retention_curves["scope"] == scope) & (retention_curves["entity_id"] == entity_id)
            ].copy()
            data["metric_label"] = data["metric"].map(metric_names)
            fig = px.line(
                data,
                x="day",
                y="retention_rate",
                color="metric_label",
                markers=True,
                category_orders={"metric_label": list(metric_names.values())},
                labels={"day": "첫 방문 후 경과 일수", "retention_rate": "누적 재방문 비율", "metric_label": "지표"},
                template="plotly_dark",
                color_discrete_sequence=["#4C8BF5", "#F3A43B", "#49A078"],
            )
            fig.update_yaxes(tickformat=".0%", range=[0, 1])
            fig.update_xaxes(dtick=5, range=[1, 30])
            fig.update_layout(title=title, height=360, margin=dict(l=10, r=15, t=50, b=45), legend_title_text="")
            return fig

        st.markdown("**전체 리텐션 곡선**")
        st.plotly_chart(retention_figure("overall", "전체", "전체 관측 코호트 · 누적 30일 리텐션"), use_container_width=True)

        root_summary = retention_summary.loc[retention_summary["scope"] == "root"].copy()
        category_summary = retention_summary.loc[retention_summary["scope"] == "category"].copy()
        mapped_roots = root_summary.loc[root_summary["entity_id"] != "대분류 없음"].sort_values("all_event_cohort_users", ascending=False)
        root_options = mapped_roots["entity_id"].tolist() + (["대분류 없음"] if (root_summary["entity_id"] == "대분류 없음").any() else [])
        if root_options:
            default_root = mapped_roots.iloc[0]["entity_id"] if not mapped_roots.empty else root_options[0]
            root_choice = st.selectbox("대분류", root_options, index=root_options.index(default_root), key="retention_root_choice")
            root_row = root_summary.loc[root_summary["entity_id"] == root_choice].iloc[0]
            if root_choice == "대분류 없음":
                category_options = ["카테고리 없음"] if (category_summary["entity_id"] == "카테고리 없음").any() else []
            else:
                category_ids = category_root_map.loc[category_root_map["root_category_id"] == root_choice, "category_id"].tolist()
                category_options = category_summary.loc[category_summary["entity_id"].isin(category_ids)].sort_values("all_event_cohort_users", ascending=False)["entity_id"].tolist()
            if category_options:
                category_choice = st.selectbox("세부 카테고리", category_options, key=f"retention_category_choice_{root_choice}")
                category_row = category_summary.loc[category_summary["entity_id"] == category_choice].iloc[0]
            else:
                category_choice = None
                category_row = None

            root_chart, category_chart = st.columns(2)
            with root_chart:
                st.plotly_chart(retention_figure("root", root_choice, f"대분류 {root_choice} · 누적 30일 리텐션"), use_container_width=True)
                st.dataframe(pd.DataFrame([root_row]), hide_index=True, use_container_width=True)
            with category_chart:
                if category_row is not None:
                    st.plotly_chart(retention_figure("category", category_choice, f"카테고리 {category_choice} · 누적 30일 리텐션"), use_container_width=True)
                    st.dataframe(pd.DataFrame([category_row]), hide_index=True, use_container_width=True)
                else:
                    st.info("선택한 대분류에 조회 가능한 세부 카테고리가 없습니다.")

        st.markdown("**30일 재방문율 상위 카테고리 (코호트 500명 이상)**")
        eligible_categories = category_summary.loc[
            (category_summary["entity_id"] != "카테고리 없음") & (category_summary["all_event_cohort_users"] >= 500)
        ].nlargest(10, "all_event_revisit_rate_30d").sort_values("all_event_revisit_rate_30d")
        retention_bar = px.bar(
            eligible_categories,
            x="all_event_revisit_rate_30d",
            y="entity_id",
            orientation="h",
            text=eligible_categories["all_event_revisit_rate_30d"].map(lambda value: f"{value:.1%}"),
            labels={"entity_id": "카테고리 ID", "all_event_revisit_rate_30d": "30일 재방문율"},
            template="plotly_dark",
            color_discrete_sequence=["#4C8BF5"],
        )
        retention_bar.update_traces(textposition="outside", cliponaxis=False, hovertemplate="카테고리 %{y}<br>30일 재방문율: %{x:.2%}<br>코호트: %{customdata[0]:,}명<extra></extra>", customdata=eligible_categories[["all_event_cohort_users"]].to_numpy())
        retention_bar.update_layout(height=380, margin=dict(l=10, r=55, t=15, b=35), showlegend=False, xaxis_tickformat=".0%")
        st.plotly_chart(retention_bar, use_container_width=True)

        st.divider()
        st.markdown("**방문 시점 기준 리텐션 코호트**")
        retention_grain = st.radio("코호트 집계 단위", ["일별", "주별", "월별"], horizontal=True, key="retention_cohort_grain")
        grain_specs = {
            "일별": {"period_label": "첫 방문일", "offset_label": "Nday", "max_offset": 30, "freq": "D"},
            "주별": {"period_label": "첫 방문 주 시작일 (월요일)", "offset_label": "Nweek", "max_offset": 12, "freq": "W"},
            "월별": {"period_label": "첫 방문 월", "offset_label": "Nmonth", "max_offset": 6, "freq": "M"},
        }
        spec = grain_specs[retention_grain]
        grain_caption = {
            "일별": "Nday는 첫 방문일 이후 경과한 UTC 달력 일수입니다.",
            "주별": "주간 코호트는 월요일부터 일요일까지이며, Nweek는 첫 방문 주 이후 경과한 주 수입니다.",
            "월별": "월간 코호트는 UTC 달력 월 기준이며, Nmonth는 첫 방문 월 이후 경과한 월 수입니다.",
        }[retention_grain]
        st.caption(f"각 사용자의 첫 이벤트를 기준으로 코호트를 구성합니다. {grain_caption} 각 셀은 해당 기간에 이벤트가 다시 발생한 고유 사용자 수와 코호트 대비 비율입니다. 관측 종료 시각(2015-09-15 21:00 UTC)까지 온전히 관측되지 않은 기간은 공란으로 표시합니다.")
        grain_cohorts = all_retention_cohorts.loc[all_retention_cohorts["grain"] == retention_grain].copy()
        cohort_dates = sorted(grain_cohorts["cohort_period"].drop_duplicates())
        if cohort_dates:
            selected_visit_cohorts = grain_cohorts.loc[
                grain_cohorts["cohort_date"].between(retention_visit_start_utc, retention_visit_end_utc)
            ]
            cohort_counts = (
                selected_visit_cohorts.groupby(["cohort_period", "offset"], observed=True)["returning_users"]
                .sum().rename("returning_users").reset_index()
            )
            cohort_sizes = (
                selected_visit_cohorts.loc[selected_visit_cohorts["offset"] == 0]
                .groupby("cohort_period", observed=True)["cohort_users"].sum()
            )
            visible_dates = sorted(cohort_sizes.index.unique())
            if visible_dates:
                count_lookup = cohort_counts.set_index(["cohort_period", "offset"])["returning_users"]
                retention_grid, count_grid, heatmap_text = [], [], []
                cutoff_day = pd.Timestamp("2015-09-15", tz="UTC")
                for cohort_date in visible_dates:
                    cohort_size = int(cohort_sizes.loc[cohort_date])
                    rate_row, count_row, text_row = [], [], []
                    for offset in range(spec["max_offset"] + 1):
                        if retention_grain == "일별":
                            target_date = cohort_date + pd.Timedelta(days=offset)
                            period_end = target_date + pd.Timedelta(days=1)
                        elif retention_grain == "주별":
                            target_date = cohort_date + pd.Timedelta(weeks=offset)
                            period_end = target_date + pd.Timedelta(weeks=1)
                        else:
                            target_date = cohort_date + pd.DateOffset(months=offset)
                            period_end = target_date + pd.DateOffset(months=1)
                        is_observed = offset == 0 or period_end <= pd.Timestamp("2015-09-15 21:00:00", tz="UTC")
                        if not is_observed:
                            rate_row.append(None)
                            count_row.append(None)
                            text_row.append("")
                            continue
                        returned = int(count_lookup.get((cohort_date, offset), 0))
                        rate = returned / cohort_size if cohort_size else 0
                        rate_row.append(rate)
                        count_row.append(returned)
                        text_row.append(f"{returned:,}<br>{rate:.0%}")
                    retention_grid.append(rate_row)
                    count_grid.append(count_row)
                    heatmap_text.append(text_row)
                row_labels = [value.strftime("%Y-%m-%d") for value in visible_dates]
                x_labels = [f"Nday {value}" if retention_grain == "일별" else f"Nweek {value}" if retention_grain == "주별" else f"Nmonth {value}" for value in range(spec["max_offset"] + 1)]
                heatmap = go.Figure(
                    data=go.Heatmap(
                        z=retention_grid,
                        x=x_labels,
                        y=row_labels,
                        text=heatmap_text,
                        texttemplate="%{text}",
                        customdata=count_grid,
                        colorscale="Blues",
                        zmin=0,
                        zmax=1,
                        colorbar={"title": "재방문율", "tickformat": ".0%"},
                        hovertemplate=f"{spec['period_label']}: %{{y}}<br>%{{x}}<br>재방문 사용자: %{{customdata:,.0f}}명<br>재방문율: %{{z:.1%}}<extra></extra>",
                    )
                )
                heatmap.update_layout(
                    template="plotly_dark",
                    height=max(650, len(row_labels) * 29),
                    margin=dict(l=20, r=20, t=20, b=45),
                    xaxis_title=f"첫 방문 후 경과 기간 ({spec['offset_label']})",
                    yaxis_title=spec["period_label"],
                    yaxis={"autorange": "reversed", "dtick": 1},
                )
                heatmap.update_xaxes(dtick=1, tickangle=0)
                st.plotly_chart(heatmap, use_container_width=True)
                default_cohort_count = {"일별": 30, "주별": 16, "월별": 12}[retention_grain]
                st.caption(f"셀 표기: 재방문 사용자 수(명) · 코호트 대비 재방문율(%). 색이 진할수록 재방문율이 높습니다. {spec['offset_label']} 0은 코호트 규모를 뜻하므로 100%입니다. 기본 범위는 최근 {default_cohort_count}개 코호트이며 날짜 선택으로 범위를 조정할 수 있습니다.")
            else:
                st.info("선택한 방문일 범위에 코호트가 없습니다.")
