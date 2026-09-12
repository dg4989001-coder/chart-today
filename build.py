def add_volume_profile(ax, df_full, bins=50, width_frac=0.14):
    """전기간 누적 거래량을 왼쪽에 가로 막대로 표시. 최대 매물대는 강조."""
    lo = float(df_full["low"].min())
    hi = float(df_full["high"].max())
    if hi <= lo:
        return
    edges = np.linspace(lo, hi, bins + 1)
    profile = np.zeros(bins)
    for _, r in df_full.iterrows():
        b_lo = int(np.clip((r["low"] - lo) / (hi - lo) * bins, 0, bins - 1))
        b_hi = int(np.clip((r["high"] - lo) / (hi - lo) * bins, 0, bins - 1))
        n_bands = b_hi - b_lo + 1
        profile[b_lo:b_hi + 1] += r["volume"] / n_bands
    if profile.max() <= 0:
        return
    profile_norm = profile / profile.max()
    max_idx = int(np.argmax(profile))
    trans = ax.get_yaxis_transform()

    # 화면에 보이는 y 범위 (현재 창 기준)
    ylo, yhi = ax.get_ylim()
    visible_max_idx = None
    visible_max_val = -1.0
    for i, v in enumerate(profile_norm):
        y_center = (edges[i] + edges[i + 1]) / 2
        if ylo <= y_center <= yhi and v > visible_max_val:
            visible_max_val = v
            visible_max_idx = i

    for i, v in enumerate(profile_norm):
        y_center = (edges[i] + edges[i + 1]) / 2
        if y_center < ylo or y_center > yhi:
            continue
        h = (edges[i + 1] - edges[i]) * 0.85
        is_max_visible = (i == visible_max_idx)
        color = VP_MAX_C if is_max_visible else VP_C
        alpha = 0.55 if is_max_visible else 0.28
        ax.barh(y_center, v * width_frac, height=h, left=0.005,
                color=color, alpha=alpha, zorder=0 if not is_max_visible else 2,
                transform=trans)

    # 최대 매물대 라벨 — 화면 안쪽에 고정 배치
    if visible_max_idx is not None:
        max_y_full = (edges[max_idx] + edges[max_idx + 1]) / 2
        max_y_vis = (edges[visible_max_idx] + edges[visible_max_idx + 1]) / 2
        # 라벨 위치: 화면 안 상단 왼쪽
        label_y_vis = max(max_y_vis, ylo + (yhi - ylo) * 0.75)
        ax.annotate(f"최대 매물대\n{int(max_y_full):,}원",
                    xy=(width_frac + 0.01, max_y_vis), xycoords=trans,
                    xytext=(0.18, 0.82), textcoords="axes fraction",
                    fontsize=8.5, color=VP_MAX_C, fontweight="bold",
                    ha="left", va="center", zorder=10,
                    bbox=dict(boxstyle="round,pad=0.35", fc="#FFFBF0",
                              ec=VP_MAX_C, lw=0.9, alpha=0.95),
                    arrowprops=dict(arrowstyle="->", color=VP_MAX_C, lw=1.3,
                                    shrinkB=3))
