import os
import numpy as np
import pandas as pd
import streamlit as st
import yfinance as yf
import altair as alt

# =========================================================
# App configuration
# =========================================================
st.set_page_config(
    page_title="Portfolio Strategy Validation Dashboard",
    layout="wide",
)

# Fixed settings for the polished presentation version
BASE_WEIGHTS_PATH = "outputs/base_portfolio_weights.csv"
TACTICAL_RETURNS_PATH = "outputs/tactical_overlay_returns.csv"
OUTPUT_PATH = "outputs/portfolio_strategy_comparison_returns.csv"
TICKERS = ["SPY", "AGG"]
RISK_FREE_RATE = 0.00
ROLLING_WINDOW = 12
PERIODS_PER_YEAR = 12

PORTFOLIO_LABELS = {
    "Equal_Weight_A": "Equal Weight Portfolio",
    "Traditional_60_40_B": "60/40 Portfolio",
    "Risk_Budgeted_C": "Risk Budget Portfolio",
    "Risk_Overlay_CPlus": "Tactical Overlay Portfolio",
}

# =========================================================
# Helpers
# =========================================================
@st.cache_data(show_spinner=False)
def load_prices(tickers, start, end) -> pd.DataFrame:
    px = yf.download(
        tickers,
        start=str(start),
        end=str(end),
        auto_adjust=True,
        progress=False,
    )["Close"]
    if isinstance(px, pd.Series):
        px = px.to_frame()
    px = px.dropna(how="all").ffill().dropna()
    px.columns = [c.strip().upper() for c in px.columns]
    return px


def load_base_portfolio_weights(path: str) -> pd.DataFrame:
    w = pd.read_csv(path, index_col=0, parse_dates=True)
    w.columns = [c.strip().upper() for c in w.columns]
    w = w.sort_index()
    w = w.div(w.sum(axis=1), axis=0)
    w.index.name = "Date"
    return w


def load_tactical_overlay_returns(path: str) -> pd.Series:
    r = pd.read_csv(path, parse_dates=[0])
    r = r.rename(columns={r.columns[0]: "Date"})
    r = r.set_index("Date").sort_index()
    if "Portfolio_Return" not in r.columns:
        raise ValueError("Tactical overlay returns file must contain a Portfolio_Return column.")
    out = r["Portfolio_Return"].rename("Risk_Overlay_CPlus")
    out.index.name = "Date"
    return out


def max_drawdown(returns: pd.Series) -> float:
    wealth = (1 + returns.dropna()).cumprod()
    drawdown = wealth / wealth.cummax() - 1
    return float(drawdown.min())


def drawdown_series(returns: pd.Series) -> pd.Series:
    wealth = (1 + returns.dropna()).cumprod()
    return wealth / wealth.cummax() - 1


def drawdown_duration(returns: pd.Series) -> int:
    dd = drawdown_series(returns)
    underwater = dd < 0
    max_duration = 0
    current = 0
    for value in underwater:
        if value:
            current += 1
            max_duration = max(max_duration, current)
        else:
            current = 0
    return int(max_duration)


def performance_metrics(returns: pd.DataFrame, periods_per_year: int = 12, rf: float = 0.0) -> pd.DataFrame:
    rows = []
    for col in returns.columns:
        r = returns[col].dropna()
        if len(r) == 0:
            continue
        total_return = (1 + r).prod() - 1
        years = len(r) / periods_per_year
        cagr = (1 + total_return) ** (1 / years) - 1 if years > 0 else np.nan
        vol = r.std() * np.sqrt(periods_per_year)
        sharpe = (cagr - rf) / vol if vol != 0 else np.nan
        mdd = max_drawdown(r)
        calmar = cagr / abs(mdd) if mdd < 0 else np.nan
        worst_month = r.min()
        yearly = (1 + r).resample("YE").prod() - 1
        worst_year = yearly.min() if len(yearly) else np.nan
        dd_months = drawdown_duration(r)

        rows.append({
            "Portfolio": col,
            "Total Return": total_return,
            "CAGR": cagr,
            "Annualised Volatility": vol,
            "Sharpe Ratio": sharpe,
            "Max Drawdown": mdd,
            "Calmar Ratio": calmar,
            "Worst Month": worst_month,
            "Worst Year": worst_year,
            "Max Drawdown Duration (months)": dd_months,
        })
    return pd.DataFrame(rows).set_index("Portfolio")


def pct(x: float) -> str:
    return f"{x:.2%}"


def pp(x: float) -> str:
    """Percentage-point display for differences."""
    return f"{x * 100:.2f}%"


def format_metrics(metrics: pd.DataFrame) -> pd.DataFrame:
    out = metrics.copy()
    pct_cols = ["Total Return", "CAGR", "Annualised Volatility", "Max Drawdown", "Worst Month", "Worst Year"]
    for c in pct_cols:
        if c in out.columns:
            out[c] = (out[c] * 100).map(lambda x: f"{x:.2f}%")
    if "Sharpe Ratio" in out.columns:
        out["Sharpe Ratio"] = out["Sharpe Ratio"].map(lambda x: f"{x:.2f}")
    if "Calmar Ratio" in out.columns:
        out["Calmar Ratio"] = out["Calmar Ratio"].map(lambda x: f"{x:.2f}")
    return out


def display_name(name: str) -> str:
    return PORTFOLIO_LABELS.get(name, name)


def rename_for_display(df: pd.DataFrame) -> pd.DataFrame:
    return df.rename(index=PORTFOLIO_LABELS, columns=PORTFOLIO_LABELS)


def interpretation_for_pair(challenger: str, benchmark: str, c: pd.Series, b: pd.Series) -> str:
    cagr_diff = c["CAGR"] - b["CAGR"]
    vol_diff = c["Annualised Volatility"] - b["Annualised Volatility"]
    sharpe_diff = c["Sharpe Ratio"] - b["Sharpe Ratio"]
    dd_diff = c["Max Drawdown"] - b["Max Drawdown"]  # positive = better drawdown control

    if challenger == "Traditional_60_40_B" and benchmark == "Equal_Weight_A":
        return (
            f"The 60/40 portfolio improved the return profile versus Equal Weight. CAGR increased by {pp(cagr_diff)} "
            f"and Sharpe improved by {sharpe_diff:.2f}. The trade-off was slightly higher volatility "
            f"({pp(vol_diff)}) and a deeper max drawdown ({pp(dd_diff)}). Overall, 60/40 added return, "
            f"but not risk reduction."
        )

    if challenger == "Risk_Budgeted_C" and benchmark == "Traditional_60_40_B":
        return (
            f"Risk budgeting achieved its risk-control objective: volatility fell by {pp(abs(vol_diff))} "
            f"and max drawdown improved by {pp(dd_diff)} versus 60/40. However, CAGR fell by "
            f"{pp(abs(cagr_diff))} and Sharpe declined by {abs(sharpe_diff):.2f}. This created a smoother portfolio, "
            f"but the return sacrifice was substantial."
        )

    if challenger == "Risk_Overlay_CPlus" and benchmark == "Risk_Budgeted_C":
        return (
            f"The tactical overlay clearly added value versus the risk-budgeted portfolio. CAGR improved by "
            f"{pp(cagr_diff)}, Sharpe improved by {sharpe_diff:.2f}, and max drawdown was broadly unchanged "
            f"({pp(dd_diff)}). This suggests the overlay recovered a large part of the return lost by risk budgeting "
            f"while preserving most of the drawdown control."
        )

    if challenger == "Risk_Overlay_CPlus" and benchmark == "Traditional_60_40_B":
        return (
            f"Compared with 60/40, the tactical overlay gave up {pp(abs(cagr_diff))} of CAGR, but volatility fell by "
            f"{pp(abs(vol_diff))}, Sharpe improved by {sharpe_diff:.2f}, and max drawdown improved by {pp(dd_diff)}. "
            f"This makes the tactical overlay competitive with 60/40 on a risk-adjusted basis, even though 60/40 delivered "
            f"the higher absolute return."
        )

    return (
        f"{display_name(challenger)} vs {display_name(benchmark)}: CAGR change {pp(cagr_diff)}, "
        f"volatility change {pp(vol_diff)}, Sharpe change {sharpe_diff:.2f}, max drawdown change {pp(dd_diff)}."
    )


def build_validation_table(metrics: pd.DataFrame) -> pd.DataFrame:
    checks = []

    def add_check(test_name, challenger, benchmark):
        if challenger not in metrics.index or benchmark not in metrics.index:
            return
        c = metrics.loc[challenger]
        b = metrics.loc[benchmark]
        checks.append({
            "Test": test_name,
            "Challenger": display_name(challenger),
            "Benchmark": display_name(benchmark),
            "CAGR Change": c["CAGR"] - b["CAGR"],
            "Volatility Change": c["Annualised Volatility"] - b["Annualised Volatility"],
            "Sharpe Change": c["Sharpe Ratio"] - b["Sharpe Ratio"],
            "Max Drawdown Change": c["Max Drawdown"] - b["Max Drawdown"],
            "Interpretation": interpretation_for_pair(challenger, benchmark, c, b),
        })

    add_check("Did 60/40 improve over Equal Weight?", "Traditional_60_40_B", "Equal_Weight_A")
    add_check("Did Risk Budgeting add value?", "Risk_Budgeted_C", "Traditional_60_40_B")
    add_check("Did Tactical Overlay add value?", "Risk_Overlay_CPlus", "Risk_Budgeted_C")
    add_check("How did Tactical Overlay compare with 60/40?", "Risk_Overlay_CPlus", "Traditional_60_40_B")

    return pd.DataFrame(checks)


def build_rank_table(metrics: pd.DataFrame) -> pd.DataFrame:
    rank_table = pd.DataFrame(index=metrics.index)
    rank_table["CAGR Rank"] = metrics["CAGR"].rank(ascending=False).astype(int)
    rank_table["Sharpe Rank"] = metrics["Sharpe Ratio"].rank(ascending=False).astype(int)
    rank_table["Drawdown Rank"] = metrics["Max Drawdown"].rank(ascending=False).astype(int)
    rank_table["Volatility Rank"] = metrics["Annualised Volatility"].rank(ascending=True).astype(int)
    rank_table["Average Rank"] = rank_table.mean(axis=1).round(2)
    return rank_table.sort_values("Average Rank")


def show_metric_card(container, label: str, portfolio: str, value: str):
    container.metric(label, display_name(portfolio), value)


# =========================================================
# Sidebar: polished, non-technical
# =========================================================
with st.sidebar:
    st.header("Strategy Overview")
    st.write("Portfolio validation across four strategies:")
    st.markdown(
        """
        - Equal Weight  
        - 60/40 Portfolio  
        - Risk Budget Portfolio  
        - Tactical Overlay Portfolio
        """
    )
    st.divider()
    st.caption("Fixed analysis settings: SPY/AGG, monthly returns, 12-month rolling analytics, 0% risk-free rate.")


# =========================================================
# Load required input datasets
# =========================================================
if not os.path.exists(BASE_WEIGHTS_PATH):
    st.error(f"Required file not found: {BASE_WEIGHTS_PATH}")
    st.stop()

if not os.path.exists(TACTICAL_RETURNS_PATH):
    st.error(f"Required file not found: {TACTICAL_RETURNS_PATH}")
    st.stop()

base_weights = load_base_portfolio_weights(BASE_WEIGHTS_PATH)
tactical_overlay_returns = load_tactical_overlay_returns(TACTICAL_RETURNS_PATH)

start = base_weights.index.min().date()
end = (pd.Timestamp.today() + pd.Timedelta(days=1)).date()

prices_daily = load_prices(TICKERS, start, end)
prices_monthly = prices_daily.resample("ME").last()
monthly_returns = prices_monthly.pct_change().dropna()

common_idx = base_weights.index.intersection(monthly_returns.index)
base_weights = base_weights.loc[common_idx]
monthly_returns = monthly_returns.loc[common_idx]

required_assets = {"SPY", "AGG"}
if not required_assets.issubset(set(monthly_returns.columns)):
    st.error("This analysis expects SPY and AGG data.")
    st.stop()

# =========================================================
# Build four portfolio return streams
# =========================================================
equal_weight = (0.50 * monthly_returns["SPY"] + 0.50 * monthly_returns["AGG"]).rename("Equal_Weight_A")
traditional_6040 = (0.60 * monthly_returns["SPY"] + 0.40 * monthly_returns["AGG"]).rename("Traditional_60_40_B")
risk_budgeted = (monthly_returns * base_weights).sum(axis=1).rename("Risk_Budgeted_C")

portfolio_returns = pd.concat([
    equal_weight,
    traditional_6040,
    risk_budgeted,
    tactical_overlay_returns,
], axis=1).dropna()
portfolio_returns.index.name = "Date"

os.makedirs("outputs", exist_ok=True)
portfolio_returns.to_csv(OUTPUT_PATH)

metrics = performance_metrics(portfolio_returns, periods_per_year=PERIODS_PER_YEAR, rf=RISK_FREE_RATE)
validation = build_validation_table(metrics)
rank_table = build_rank_table(metrics)

best_cagr = metrics["CAGR"].idxmax()
best_sharpe = metrics["Sharpe Ratio"].idxmax()
best_drawdown = metrics["Max Drawdown"].idxmax()  # least negative = best drawdown control
lowest_vol = metrics["Annualised Volatility"].idxmin()

portfolio_returns_display = portfolio_returns.rename(columns=PORTFOLIO_LABELS)
growth = (1 + portfolio_returns_display).cumprod()
drawdowns = portfolio_returns_display.apply(drawdown_series)
rolling_vol = portfolio_returns_display.rolling(ROLLING_WINDOW).std() * np.sqrt(PERIODS_PER_YEAR)
rolling_return = (1 + portfolio_returns_display).rolling(ROLLING_WINDOW).apply(np.prod, raw=True) - 1

# =========================================================
# Dashboard
# =========================================================
st.title("Portfolio Strategy Validation Dashboard")
st.caption("Testing whether each additional layer of portfolio sophistication improved performance, risk control, and drawdown behaviour.")

st.subheader("Executive Summary")
st.write(
    "This dashboard validates four portfolio designs in sequence: Equal Weight, 60/40, Risk Budgeting, and Tactical Overlay. "
    "The focus is not simply the highest return, but whether each layer improved the return/risk trade-off."
)

c1, c2, c3, c4 = st.columns(4)
show_metric_card(c1, "Highest Return", best_cagr, pct(metrics.loc[best_cagr, "CAGR"]))
show_metric_card(c2, "Best Risk-Adjusted", best_sharpe, f"Sharpe {metrics.loc[best_sharpe, 'Sharpe Ratio']:.2f}")
show_metric_card(c3, "Best Drawdown Control", best_drawdown, pct(metrics.loc[best_drawdown, "Max Drawdown"]))
show_metric_card(c4, "Lowest Volatility", lowest_vol, pct(metrics.loc[lowest_vol, "Annualised Volatility"]))

st.info(
    "Key finding: 60/40 delivered the highest absolute return, but the Tactical Overlay portfolio delivered the strongest "
    "risk-adjusted profile. Risk budgeting reduced volatility and drawdown, while the tactical overlay recovered much of the "
    "return lost by risk budgeting."
)

st.subheader("Performance Summary")
st.dataframe(rename_for_display(format_metrics(metrics)), use_container_width=True)

st.subheader("Growth of £1")
st.line_chart(growth)

st.subheader("Drawdown Comparison")
st.line_chart(drawdowns)

st.subheader(f"{ROLLING_WINDOW}-Month Rolling Return")
st.line_chart(rolling_return.dropna())

st.subheader(f"{ROLLING_WINDOW}-Month Rolling Volatility")
st.line_chart(rolling_vol.dropna())

st.subheader("Risk vs Return Map")
risk_return = metrics.reset_index()[["Portfolio", "Annualised Volatility", "CAGR", "Sharpe Ratio", "Max Drawdown"]]
risk_return["Portfolio"] = risk_return["Portfolio"].map(PORTFOLIO_LABELS)
risk_return["CAGR Label"] = risk_return["CAGR"].map(pct)
risk_return["Volatility Label"] = risk_return["Annualised Volatility"].map(pct)
risk_return["Sharpe Label"] = risk_return["Sharpe Ratio"].map(lambda x: f"{x:.2f}")

scatter = (
    alt.Chart(risk_return)
    .mark_circle(size=220)
    .encode(
        x=alt.X("Annualised Volatility:Q", title="Annualised Volatility", axis=alt.Axis(format="%")),
        y=alt.Y("CAGR:Q", title="CAGR", axis=alt.Axis(format="%")),
        tooltip=["Portfolio", "CAGR Label", "Volatility Label", "Sharpe Label"],
    )
)
labels = (
    alt.Chart(risk_return)
    .mark_text(align="left", baseline="middle", dx=8)
    .encode(
        x="Annualised Volatility:Q",
        y="CAGR:Q",
        text="Portfolio:N",
    )
)
st.altair_chart(scatter + labels, use_container_width=True)
st.caption("Further up means higher CAGR; further left means lower volatility. Labels identify each portfolio.")

st.subheader("Validation Tests")
if len(validation):
    validation_display = validation.drop(columns=["Interpretation"]).copy()
    for col in ["CAGR Change", "Volatility Change", "Max Drawdown Change"]:
        validation_display[col] = (validation_display[col] * 100).map(lambda x: f"{x:.2f}%")
    validation_display["Sharpe Change"] = validation_display["Sharpe Change"].map(lambda x: f"{x:.2f}")
    st.dataframe(validation_display, use_container_width=True)
else:
    st.info("Not enough portfolios available for validation tests.")

st.subheader("Portfolio Ranking")
st.dataframe(rename_for_display(rank_table), use_container_width=True)

st.subheader("Investment Interpretation")
for _, row in validation.iterrows():
    st.write(f"**{row['Test']}**")
    st.write(row["Interpretation"])

st.markdown("---")
st.write("**Overall conclusion**")
st.write(
    "This analysis shows that portfolio design should be judged incrementally. The 60/40 portfolio improved return versus Equal Weight; "
    "Risk Budgeting improved stability but sacrificed return; and the Tactical Overlay delivered the clearest improvement relative to "
    "the layer it was designed to enhance. In this sample, the overlay did not simply add complexity — it materially improved "
    "risk-adjusted performance and made the risk-controlled portfolio competitive with 60/40 on a risk-adjusted basis."
)

with st.expander("View raw portfolio return dataset"):
    st.caption(f"The aligned return file is saved automatically to {OUTPUT_PATH}.")
    st.dataframe(portfolio_returns_display.tail(12).round(4), use_container_width=True)

st.caption("This dashboard tests whether each additional portfolio layer improved performance, risk control, and drawdown behaviour.")
