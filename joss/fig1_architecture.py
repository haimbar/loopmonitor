#!/usr/bin/env python3
"""Generate Figure 1: loopmonitor IPC architecture diagram.

Outputs:
  fig1_architecture.svg  – for markdown / web
  fig1_architecture.pdf  – for LaTeX / JOSS submission
"""

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch

# ── palette ──────────────────────────────────────────────────────────────────
C_HEADER    = "#1b2a4a"   # dark navy  – column headers
C_INIT      = "#dce8f5"   # pale blue  – startup boxes
C_INIT_ED   = "#4a7fbf"
C_LOOP      = "#fef3cd"   # amber      – [loop running]
C_LOOP_ED   = "#c8960c"
C_SIGNAL    = "#d6eedd"   # green      – signal handler boxes
C_SIGNAL_ED = "#3a7d52"
C_CLI       = "#e8dff5"   # lavender   – CLI boxes
C_CLI_ED    = "#6a4caf"
C_ARROW     = "#c0392b"   # red        – SIGUSR1 arrow
C_VARROW    = "#555555"   # dark grey  – vertical flow arrows
C_FIFO      = "#2980b9"   # blue       – FIFO link arrow
MONO        = "DejaVu Sans Mono"

# ── figure ───────────────────────────────────────────────────────────────────
fig, ax = plt.subplots(figsize=(13, 9))
ax.set_xlim(0, 13)
ax.set_ylim(0, 9)
ax.axis("off")
fig.patch.set_facecolor("white")

# ── helpers ──────────────────────────────────────────────────────────────────
def box(x, y, w, h, label, fc, ec, fontsize=11, bold=False, mono=True):
    rect = FancyBboxPatch(
        (x - w / 2, y - h / 2), w, h,
        boxstyle="round,pad=0.08",
        facecolor=fc, edgecolor=ec, linewidth=1.3, zorder=3,
    )
    ax.add_patch(rect)
    ff = MONO if mono else "sans-serif"
    ax.text(x, y, label, ha="center", va="center",
            fontsize=fontsize, fontfamily=ff,
            fontweight="bold" if bold else "normal",
            color="#1a1a1a", zorder=4, wrap=False)

def header(x, y, w, h, label):
    rect = FancyBboxPatch(
        (x - w / 2, y - h / 2), w, h,
        boxstyle="round,pad=0.08",
        facecolor=C_HEADER, edgecolor=C_HEADER, linewidth=0, zorder=3,
    )
    ax.add_patch(rect)
    ax.text(x, y, label, ha="center", va="center",
            fontsize=12, fontfamily="sans-serif",
            fontweight="bold", color="white", zorder=4)

def varrow(x, y0, y1):
    """Downward vertical arrow from y0 to y1 (y0 > y1)."""
    ax.annotate("", xy=(x, y1 + 0.07), xytext=(x, y0 - 0.07),
                arrowprops=dict(arrowstyle="->", color=C_VARROW,
                                lw=1.2, mutation_scale=12),
                zorder=2)

def curved_arrow(x0, y0, x1, y1, label, color, rad, fontsize=10, lw=1.8,
                 label_offset=(0, 0.18), linestyle="solid", arrowstyle="->"):
    """Curved arrow (or line) from (x0,y0) to (x1,y1) with a label."""
    ax.annotate("", xy=(x1, y1), xytext=(x0, y0),
                arrowprops=dict(
                    arrowstyle=arrowstyle, color=color, lw=lw,
                    mutation_scale=14,
                    connectionstyle=f"arc3,rad={rad}",
                    linestyle=linestyle,
                ),
                zorder=5)
    mx = (x0 + x1) / 2 + label_offset[0]
    my = (y0 + y1) / 2 + label_offset[1]
    ax.text(mx, my, label, ha="center", va="bottom",
            fontsize=fontsize, color=color, fontstyle="italic", zorder=6)

# ── column centres and box geometry ──────────────────────────────────────────
LX, RX = 3.0, 10.0   # left / right column x-centres
BW = 4.4              # box width  (gap between edges = RX-LX-BW = 2.6 units)
BH = 0.60             # normal box height

# Box edges used for arrow attachment
L_RIGHT = LX + BW / 2   # 5.20
R_LEFT  = RX - BW / 2   # 7.80

# ── LEFT column: y positions (top → bottom) ──────────────────────────────────
# Setup phase: L[0..3]   Loop phase: L[4..7]
HEADER_LY = 8.50
L = [7.75, 7.00, 6.25, 5.50,   4.10, 3.20, 2.40, 1.60]
#    0      1      2      3       4      5      6      7
# installs handler at L[3]=5.50; big gap → loop starts at L[4]=4.10

header(LX, HEADER_LY, BW, 0.58, "Your program  (PID 12345)")
box(LX, L[0], BW, BH, "ipc_range()  starts",                  C_INIT,   C_INIT_ED,   bold=True)
box(LX, L[1], BW, BH, "creates  ~/.ipc/12345.fifo  (0600)",   C_INIT,   C_INIT_ED)
box(LX, L[2], BW, BH, "registers in  ~/.ipc/registry.json",   C_INIT,   C_INIT_ED)
box(LX, L[3], BW, BH, "installs  SIGUSR1  handler",           C_INIT,   C_INIT_ED)
box(LX, L[4], BW, 0.80, "[ loop running … ]",                 C_LOOP,   C_LOOP_ED,   fontsize=12, bold=True)
box(LX, L[5], BW, BH, "signal handler fires",                 C_SIGNAL, C_SIGNAL_ED, bold=True)
box(LX, L[6], BW, BH, 'reads  "peek\\n"  from FIFO',         C_SIGNAL, C_SIGNAL_ED)
box(LX, L[7], BW, BH, "prints status to stdout",              C_SIGNAL, C_SIGNAL_ED)

ax.text(LX, 0.95, "[ loop continues … ]",
        ha="center", va="center", fontsize=11, fontfamily=MONO,
        color=C_LOOP_ED, fontweight="bold")

# Left vertical arrows
for i in range(len(L) - 1):
    gap_before_loop = (i == 3)   # large visual gap between setup and loop
    y_end = L[i + 1] + (0.15 if gap_before_loop else 0)
    varrow(LX, L[i], y_end)
varrow(LX, L[7], 0.95 + 0.15)

# ── RIGHT column: y positions (top → bottom) ─────────────────────────────────
# CLI items start at R[0] ≈ 4.95, below L[3]=5.50 (after setup completes)
HEADER_RY = 8.50
R = [4.95, 4.20, 3.45, 2.70, 1.95]
#    0      1      2      3      4
# R[0] is below L[3]=5.50  →  CLI is issued after the loop is already running

header(RX, HEADER_RY, BW, 0.58, "Your second terminal")
box(RX, R[0], BW, BH, "$ ipc peek 12345",                         C_CLI, C_CLI_ED, bold=True)
box(RX, R[1], BW, BH, "opens  ~/.ipc/12345.fifo  for writing",    C_CLI, C_CLI_ED)
box(RX, R[2], BW, BH, 'writes  "peek\\n"  (atomic, ≤ PIPE_BUF)', C_CLI, C_CLI_ED)
box(RX, R[3], BW, BH, "sends  SIGUSR1  to PID 12345",             C_CLI, C_CLI_ED, bold=True)
box(RX, R[4], BW, BH, "(returns / exits)",                        C_CLI, C_CLI_ED)

# Right vertical arrows
for i in range(len(R) - 1):
    varrow(RX, R[i], R[i + 1])

# ── Cross-column arrows ───────────────────────────────────────────────────────

# 1. Shared FIFO: "creates FIFO" (L[1]) ──► "opens FIFO" (R[1])
#    Curved dashed arrow going right and down across the inter-column gap.
curved_arrow(
    L_RIGHT, L[1],        # source: right edge of "creates FIFO" box
    R_LEFT,  R[1],        # dest:   left edge of "opens FIFO" box
    label="~/.ipc/12345.fifo  (shared)",
    color=C_FIFO, rad=-0.25, lw=1.4, fontsize=10,
    label_offset=(0, 0.20),
    linestyle="dashed", arrowstyle="-",
)

# 2. SIGUSR1 signal: "sends SIGUSR1" (R[3]) ──► [loop running] / handler (L[4])
#    Curved arrow going left and up.
curved_arrow(
    R_LEFT,  R[3],        # source: left edge of "sends SIGUSR1" box
    L_RIGHT, L[4],        # dest:   right edge of "[loop running]" box
    label="SIGUSR1  delivered",
    color=C_ARROW, rad=0.25, lw=2.0, fontsize=10,
    label_offset=(0, 0.20),
)

# ── "time gap" annotation between setup and CLI ───────────────────────────────
# Small label in the right column's empty space explaining the gap
ax.text(RX, 6.30, "(loop already running)", ha="center", va="center",
        fontsize=11, color="#445566", fontstyle="italic", fontfamily="sans-serif")
ax.text(RX, 5.75, "↓  user types  ipc peek  ↓", ha="center", va="center",
        fontsize=11, color="#445566", fontstyle="italic", fontfamily="sans-serif")

# ── Legend ────────────────────────────────────────────────────────────────────
legend_items = [
    (C_INIT,   C_INIT_ED,   "Startup / initialisation"),
    (C_LOOP,   C_LOOP_ED,   "Loop execution"),
    (C_SIGNAL, C_SIGNAL_ED, "Signal handler"),
    (C_CLI,    C_CLI_ED,    "CLI (ipc command)"),
]
lx0, ly0 = 0.35, 0.52
for i, (fc, ec, lbl) in enumerate(legend_items):
    bx = lx0 + i * 3.10
    rect = FancyBboxPatch((bx, ly0 - 0.14), 0.38, 0.28,
                          boxstyle="round,pad=0.04",
                          facecolor=fc, edgecolor=ec, linewidth=1.0, zorder=3)
    ax.add_patch(rect)
    ax.text(bx + 0.50, ly0, lbl, va="center", fontsize=10,
            fontfamily="sans-serif", color="#333333")

# ── save ─────────────────────────────────────────────────────────────────────
for ext in ("svg", "pdf"):
    path = f"fig1_architecture.{ext}"
    fig.savefig(path, format=ext, bbox_inches="tight",
                facecolor="white", dpi=300)
    print(f"Saved {path}")

plt.close(fig)
