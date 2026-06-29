import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import numpy as np
import pandas as pd
from pathlib import Path
from analysis import rsi as _calc_rsi, macd as _calc_macd

plt.rcParams["figure.facecolor"] = "#131722"
plt.rcParams["axes.facecolor"] = "#131722"
plt.rcParams["axes.edgecolor"] = "#2a2e39"
plt.rcParams["axes.labelcolor"] = "#9ea5b5"
plt.rcParams["text.color"] = "#d1d4dc"
plt.rcParams["xtick.color"] = "#9ea5b5"
plt.rcParams["ytick.color"] = "#9ea5b5"
plt.rcParams["grid.color"] = "#2a2e39"
plt.rcParams["grid.alpha"] = 0.5
plt.rcParams["legend.facecolor"] = "#1e222d"
plt.rcParams["legend.edgecolor"] = "#2a2e39"
plt.rcParams["legend.labelcolor"] = "#d1d4dc"

CHART_DIR = Path(__file__).parent / "charts"
CHART_DIR.mkdir(exist_ok=True)

COLORS = {
    "up": "#26a69a", "down": "#ef5350",
    "ema9": "#00e5ff", "ema21": "#ffea00",
    "sma50": "#ffa726", "sma200": "#ef5350",
    "support": "#66bb6a", "resistance": "#ef5350",
    "volume_up": "#26a69a", "volume_down": "#ef5350",
    "macd": "#4fc3f7", "signal": "#ffa726",
    "rsi": "#ab47bc",
}

def generate_chart(symbol: str, hist: pd.DataFrame, analysis: dict = None) -> str | None:
    if hist.empty or len(hist) < 20:
        return None

    close = hist["Close"]
    has_vol = "Volume" in hist.columns and hist["Volume"].sum() > 0
    has_macd = analysis and analysis.get("macd") is not None

    if has_macd:
        fig, (ax1, ax2, ax3, ax4) = plt.subplots(4, 1, figsize=(13, 10),
            gridspec_kw={"height_ratios": [3, 1, 1, 1]})
    else:
        fig, (ax1, ax2, ax3) = plt.subplots(3, 1, figsize=(13, 9),
            gridspec_kw={"height_ratios": [3, 1, 1]})

    dates = hist.index

    # Main price chart
    up = close.diff().fillna(0) >= 0
    ax1.plot(dates, close, color="#4fc3f7", linewidth=1.2, alpha=0.3)
    ax1.scatter(dates[up], close[up], color=COLORS["up"], s=4, marker=".", zorder=3)
    ax1.scatter(dates[~up], close[~up], color=COLORS["down"], s=4, marker=".", zorder=3)

    if analysis:
        ema9 = close.ewm(span=9).mean()
        ema21 = close.ewm(span=21).mean()
        ax1.plot(dates, ema9, color=COLORS["ema9"], linewidth=1, alpha=0.7, label="EMA9")
        ax1.plot(dates, ema21, color=COLORS["ema21"], linewidth=1, alpha=0.7, label="EMA21")

        if analysis.get("sma50"):
            sma50 = close.rolling(50).mean()
            ax1.plot(dates, sma50, color=COLORS["sma50"], linewidth=1, alpha=0.6, label="SMA50")
        if analysis.get("sma200"):
            sma200 = close.rolling(200).mean()
            ax1.plot(dates, sma200, color=COLORS["sma200"], linewidth=1, alpha=0.6, label="SMA200")

        if analysis.get("bb_upper") and analysis.get("bb_lower"):
            ax1.fill_between(dates, analysis["bb_upper"], analysis["bb_lower"],
                             alpha=0.06, color="#66bb6a", label="Bollinger")

        if analysis.get("support"):
            ax1.axhline(y=analysis["support"], color=COLORS["support"], linestyle="--",
                        alpha=0.5, linewidth=0.8)
        if analysis.get("resistance"):
            ax1.axhline(y=analysis["resistance"], color=COLORS["resistance"], linestyle="--",
                        alpha=0.5, linewidth=0.8)

    ax1.set_title(f"{symbol} — تحليل فني", fontsize=13, fontweight="bold", pad=12, color="#d1d4dc")
    ax1.set_ylabel("السعر", fontsize=9)
    ax1.legend(loc="upper left", fontsize=7, ncol=4, columnspacing=0.8)
    ax1.grid(True, alpha=0.15)

    # Volume
    if has_vol:
        vol_colors = [COLORS["volume_up"] if v >= 0 else COLORS["volume_down"] for v in close.diff().fillna(0)]
        ax2.bar(dates, hist["Volume"], color=vol_colors, alpha=0.4, width=0.8)
        if analysis and analysis.get("volume_ratio"):
            avg_vol = hist["Volume"].mean()
            ax2.axhline(y=avg_vol, color="#ffa726", linestyle=":", alpha=0.4, linewidth=0.7)
            ax2.text(dates[-1], avg_vol, f"  {analysis['volume_ratio']}x", fontsize=6, color="#ffa726")
        ax2.set_ylabel("الحجم", fontsize=9)
        ax2.grid(True, alpha=0.15)
    else:
        ax2.set_visible(False)

    # MACD
    if has_macd:
        m_line, sig, hist_macd = _calc_macd(close)
        macd_dates = dates[-len(m_line):]
        ax4.plot(macd_dates, m_line, color=COLORS["macd"], linewidth=1.2, label="MACD")
        ax4.plot(macd_dates, sig, color=COLORS["signal"], linewidth=1.2, label="Signal")
        macd_colors = [COLORS["up"] if h >= 0 else COLORS["down"] for h in hist_macd[-len(macd_dates):]]
        ax4.bar(macd_dates, hist_macd[-len(macd_dates):], color=macd_colors, alpha=0.4, width=0.8)
        ax4.axhline(y=0, color="#555", linewidth=0.5)
        ax4.set_ylabel("MACD", fontsize=9)
        ax4.legend(loc="upper left", fontsize=7)
        ax4.grid(True, alpha=0.15)
    else:
        # RSI subplot if no MACD info
        pass

    # RSI
    rsi_val = analysis.get("rsi") if analysis else None
    if rsi_val:
        rsi_series = _calc_rsi(close)
        rsi_dates = dates[-len(rsi_series):]
        ax3.plot(rsi_dates, rsi_series, color=COLORS["rsi"], linewidth=1.3, label=f"RSI ({rsi_val:.0f})")
        ax3.axhline(y=70, color="#ef5350", linestyle="--", alpha=0.4)
        ax3.axhline(y=30, color="#66bb6a", linestyle="--", alpha=0.4)
        ax3.fill_between(rsi_dates, 70, 30, alpha=0.03, color="#fff")
        ax3.set_ylabel("RSI", fontsize=9)
        ax3.set_ylim(0, 100)
        ax3.legend(loc="upper left", fontsize=7)
        ax3.grid(True, alpha=0.15)
    else:
        ax3.set_visible(False)

    ax1.xaxis.set_major_formatter(mdates.DateFormatter("%d/%m"))
    fig.autofmt_xdate()

    # Signal summary in title area
    if analysis and analysis.get("signal_strength"):
        ss = analysis["signal_strength"]
        color = "#26a69a" if ss > 0 else "#ef5350"
        fig.suptitle(f"قوة الإشارة: {ss:+.0f}  |  RSI: {rsi_val:.0f}" if rsi_val else f"قوة الإشارة: {ss:+.0f}",
                     fontsize=9, color=color, y=0.98)

    path = CHART_DIR / f"{symbol.replace('.', '_').replace('^', '')}.png"
    plt.tight_layout(pad=1.5)
    fig.savefig(str(path), dpi=130, bbox_inches="tight", facecolor="#131722")
    plt.close(fig)
    return str(path)
