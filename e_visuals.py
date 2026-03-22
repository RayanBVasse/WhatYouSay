import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

DEFAULT_BAR_COLORS = [
    "#2d8cb6",
    "#5aa7cc",
    "#84bfd9",
    "#a7d1e5",
    "#6c8fbe",
    "#8d9ecb",
    "#4a7ba7",
    "#6a94ba",
    "#9aaed0",
    "#4f9e93",
]


def save_bar(
    data: dict,
    title: str,
    outpath: str,
    *,
    palette=None,
    figsize=(6.0, 3.6),
    ylabel=None,
):
    keys = list(data.keys())
    vals = [data[k] for k in keys]

    fig, ax = plt.subplots(figsize=figsize)
    ax.set_facecolor("#f8fbff")

    if vals:
        colors = palette or DEFAULT_BAR_COLORS
        bar_colors = [colors[i % len(colors)] for i in range(len(vals))]
        ax.bar(keys, vals, color=bar_colors, edgecolor="#33586d", linewidth=0.7)
    else:
        ax.text(0.5, 0.5, "No data", ha="center", va="center", transform=ax.transAxes)

    ax.set_title(title, fontsize=12)
    if ylabel:
        ax.set_ylabel(ylabel)
    ax.grid(axis="y", linestyle="--", linewidth=0.6, alpha=0.35)
    ax.set_axisbelow(True)
    ax.tick_params(axis="x", rotation=35, labelsize=9)
    ax.tick_params(axis="y", labelsize=9)
    fig.tight_layout()
    fig.savefig(outpath, dpi=190)
    plt.close(fig)


def save_pie(data: dict, title: str, outpath: str, *, colors=None, figsize=(5.0, 3.8)):
    keys = list(data.keys())
    vals = [data[k] for k in keys]

    fig, ax = plt.subplots(figsize=figsize)
    ax.set_facecolor("#f8fbff")

    if sum(vals) > 0:
        slice_colors = colors or ["#2c9db3", "#d97867", "#8aa6d7", "#8dc8a3"]
        wedges, texts, autotexts = ax.pie(
            vals,
            labels=keys,
            colors=slice_colors[: len(vals)],
            startangle=125,
            autopct="%1.1f%%",
            wedgeprops={"linewidth": 1.0, "edgecolor": "#ffffff"},
            textprops={"fontsize": 9},
        )
        for txt in autotexts:
            txt.set_color("#183242")
            txt.set_fontsize(9)
    else:
        ax.text(0.5, 0.5, "No data", ha="center", va="center", transform=ax.transAxes)

    ax.set_title(title, fontsize=12)
    ax.axis("equal")
    fig.tight_layout()
    fig.savefig(outpath, dpi=190)
    plt.close(fig)


def save_line(
    y,
    title: str,
    outpath: str,
    xlabel="Message index",
    ylabel="Value",
    *,
    figsize=(6.6, 3.5),
):
    fig, ax = plt.subplots(figsize=figsize)
    ax.set_facecolor("#f8fbff")

    xs = range(len(y))
    ax.plot(xs, y, color="#2f86b7", linewidth=1.25)
    ax.axhline(0, color="#6f8493", linewidth=0.8, linestyle="--", alpha=0.7)

    ax.set_title(title, fontsize=12)
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    ax.grid(axis="y", linestyle="--", linewidth=0.6, alpha=0.35)
    ax.set_axisbelow(True)
    ax.tick_params(labelsize=9)
    fig.tight_layout()
    fig.savefig(outpath, dpi=190)
    plt.close(fig)


