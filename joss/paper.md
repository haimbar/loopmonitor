---
title: 'loopmonitor: On-Demand Inspection and Control of Long-Running Loops in Python and R'
tags:
  - Python
  - R
  - MCMC
  - iterative computation
  - statistical computing
  - loop monitoring
authors:
  - name: Haim Bar
    orcid: 0000-0002-5496-9699
    affiliation: 1
affiliations:
  - name: Department of Statistics, University of Connecticut, USA
    index: 1
date: 31 May 2026
bibliography: paper.bib
---

# Summary

`loopmonitor` is a Python package—with a companion R package of the same
name—that enables on-demand inspection and graceful external control of a
running loop from a second terminal, without modifying the program while it
runs and without connecting to any cloud service.  The analyst instruments a
loop with a single function call; the `ipc` command-line tool then supports
querying current status, streaming live updates, plotting tracked quantities,
changing parameter values during runtime, saving timestamped checkpoints, and stopping
the loop cleanly—all while the computation continues uninterrupted between
commands.

# Statement of Need

Long-running iterative procedures—Markov chain Monte Carlo (MCMC) samplers
[@gelman1992], stochastic optimizers, simulation studies, cross-validation
sweeps—are effectively opaque once launched.  The standard practices are to
add `print` statements *before* the run (requiring a restart if anything was
forgotten), to tail a pre-configured log file, or to wait and hope.  None of
these options allows the analyst to *react* to what the loop is doing: to stop
early when convergence is clear, to adjust a parameter when divergence is
detected, or to save a snapshot before stepping away from the machine.

Existing tools address parts of this problem but not all of it.  Progress bars
(`tqdm` [@tqdm] in Python; `progressr` [@progressr] in R) report completion
percentage and estimated time remaining, but offer no mechanism for inspecting
computed values or intervening in the loop.  Experiment-tracking platforms such
as TensorBoard [@tensorboard], Weights & Biases [@wandb], and MLflow [@mlflow]
provide rich visualizations and persistent run history, but require the analyst
to configure logging *before* the run and to connect to a local or
cloud-hosted server.  Neither class of tools provides a way to stop a loop
cleanly, change a parameter value, or save a snapshot from outside the
running process.

`loopmonitor` fills this gap with a *minimal instrumentation* and *maximal control*
design.  The target audience is anyone who runs long iterative computations in
Python or R on a local workstation or HPC cluster: statisticians running MCMC
chains, machine-learning practitioners tuning neural networks, and researchers
conducting large-scale simulation studies.

Table 1 summarises how `loopmonitor` compares with the most commonly used
alternatives across the capabilities that matter most for interactive loop
control.

| Feature | loopmonitor | TensorBoard | Weights & Biases | tqdm |
|:---------------------------------------------|:--------------:|:--------------:|:--------------:|:--------------:|
| No setup / no account | $\checkmark$ | $\checkmark$ | $\times$¹ | $\checkmark$ |
| No cloud / all local | $\checkmark$ | $\checkmark$ | $\times$ | $\checkmark$ |
| Works with any Python code | $\checkmark$ | partial² | partial² | $\checkmark$ |
| Works with R code | $\checkmark$ | $\times$ | $\times$ | $\times$ |
| On-demand status query | $\checkmark$ | $\times$³ | $\times$³ | $\times$ |
| Live streaming (`tail`) | $\checkmark$ | $\times$ | $\times$ | $\times$ |
| Graceful loop exit (`continue`) | $\checkmark$ | $\times$ | $\times$ | $\times$ |
| Graceful program stop (`break`) | $\checkmark$ | $\times$ | $\times$ | $\times$ |
| Mid-run value injection (`set`) | $\checkmark$ | $\times$ | $\times$ | $\times$ |
| Pause / resume process | $\checkmark$ | $\times$ | $\times$ | $\times$ |
| Desktop notifications | $\checkmark$ | $\times$ | $\times$ | $\times$ |
| Mid-run snapshots (`checkpoint`) | $\checkmark$ | $\times$ | $\times$ | $\times$ |
| Call stack inspection | $\checkmark$ | $\times$ | $\times$ | $\times$ |
| Memory usage query | $\checkmark$ | $\times$ | $\times$ | $\times$ |
| Persistent metric history | $\times$^4^ | $\checkmark$ | $\checkmark$ | $\times$ |
| Web UI / experiment comparison | $\times$ | $\checkmark$ | $\checkmark$ | $\times$ |

: Capability comparison with related tools. \label{tab:comparison}

¹ Weights & Biases requires a free account and an internet connection for its
cloud-based logging service.

² TensorBoard and W&B work best when their logging APIs are called at every
step; adding them to arbitrary code requires restructuring around their
callback model.

³ Both tools display currently logged values in a browser, but only for runs
that were instrumented with their APIs *before* they started.  `ipc peek` can
query any `ipc_range`-instrumented loop at any time, including after the fact.

^4^ `loopmonitor` retains only the most recent state snapshot.  For a full
per-iteration history, log to a file inside the loop or use TensorBoard/W&B.

# Implementation

**Python backend.**  When `ipc_range()` starts, it creates a named FIFO in
`~/.ipc/` with owner-only permissions (mode `0600`) and installs a `SIGUSR1`
handler.  The CLI writes a command string to the FIFO and signals the process;
the handler wakes, reads all queued commands atomically, and dispatches them.
Per-iteration overhead is limited to a single JSON state-file write (tens of
microseconds), negligible for the MCMC steps and mini-batch updates that
`loopmonitor` targets.  A persistent write-end file descriptor prevents
spurious end-of-file between CLI invocations, following standard POSIX
practice [@stevens2013].  Figure 1 illustrates the full message flow for a
`peek` command.

![Message flow for the Python backend.  The CLI writes atomically to the named
FIFO and signals the process; the signal handler wakes, reads all queued
commands, and returns control to the loop body.](fig1_architecture.pdf){#fig:arch}

**R backend.**  R's batch mode (`Rscript`) repurposes `SIGUSR1` for its own
save-and-quit handler, making signal-based delivery unreliable from user code.
The R backend therefore uses a polled command-file: the CLI writes commands to
a plain file; the loop functions check for and atomically consume it (via
rename-before-read) at each iteration boundary.  The user-visible interface and
`ipc` CLI are identical for both languages.

**Security.**  Three independent layers protect against interference on shared
machines: owner-only directory and FIFO permissions; symlink-safe file opens
(`O_NOFOLLOW` with post-open `fstat` ownership verification) that close the
TOCTOU (time-of-check to time-of-use) window; and safe value parsing (`ast.literal_eval` in Python; an
AST-walking `.safe_eval` in R) that accepts only literal constants and rejects
function calls, attribute access, and import statements.

**Testing.**  The Python package is distributed with 126 pytest tests across ten
modules, covering FIFO lifecycle and permissions, signal handler installation
and dispatch, all 14 CLI commands, registry operations, state serialization,
safe-value parsing, and multiprocess isolation.

# Usage

A Python training loop is instrumented with one import and one function-name change:

```python
from loopmonitor import ipc_range

for step in ipc_range(10_000, label="training"):
    loss = train_one_step()
    step.track(loss=loss)
```

While the loop runs, a second terminal provides full control:

```
$ ipc peek 12345           # snapshot: iteration, elapsed, ETA, tracked values
$ ipc tail 12345           # live scrolling status (Ctrl-C to stop)
$ ipc plot 12345           # open matplotlib trace of all tracked sequences
$ ipc set 12345 lr=0.001   # inject a new parameter value mid-run
$ ipc continue 12345       # exit loop cleanly after the current iteration
$ ipc checkpoint 12345     # save timestamped JSON snapshot without stopping
$ ipc notify 12345 "loss < 0.05"   # desktop alert when condition is met
```

The R interface provides drop-in equivalents (`ipc_for`, `ipc_while`,
`ipc_repeat`, `ipc_track`, `ipc_get`) using the same `ipc` CLI.  The Python
package is available on PyPI (`pip install loopmonitor`); the R package is
installed from GitHub (`devtools::install_github("haimbar/loopmonitor-r")`).

Table 2 lists the full set of `ipc` commands available in both the Python and
R packages.

| Command | Description |
|:-----------------------------|:--------------------------------------------------------------|
| `ipc list` | List all registered processes with PID, label, and start time |
| `ipc peek <pid>` | Print iteration, elapsed time, ETA, and tracked values |
| `ipc tail <pid>` | Stream live status updates every 2 s (Ctrl-C to stop) |
| `ipc plot <pid>` | Display a matplotlib trace of all tracked sequences |
| `ipc set <pid> k=v` | Inject a value readable via `step.get()` without stopping |
| `ipc continue <pid>` | Exit the loop after the current iteration; script continues |
| `ipc break <pid>` | Stop the program after the current iteration; save JSON snapshot |
| `ipc pause <pid>` | Suspend the process (SIGSTOP / OS-level freeze) |
| `ipc resume <pid>` | Resume a suspended process (SIGCONT) |
| `ipc notify <pid> "expr"` | Send a desktop alert when a condition on tracked values is met |
| `ipc checkpoint <pid>` | Save a timestamped JSON snapshot without stopping |
| `ipc stack <pid>` | Print the Python call stack to the process stdout |
| `ipc memory <pid>` | Print resident-set-size (RSS) memory usage |
| `ipc clean` | Remove stale registry entries after abnormal termination |

: Full `ipc` command reference. \label{tab:commands}

Full documentation for each package is available in the
[Python README](https://github.com/haimbar/loopmonitor#readme) and the
[R README](https://github.com/haimbar/loopmonitor-r#readme).

# Limitations

**POSIX only.**  `loopmonitor` relies on named FIFOs and `SIGUSR1`, both of
which are POSIX features unavailable on native Windows.  The package works on
Windows through WSL (Windows Subsystem for Linux); `ipc plot` additionally
requires a display (WSLg on Windows 11, or a third-party X server on older
setups).

**One monitored loop per process.**  Nesting two `ipc_range` calls is not
supported; the inner loop would overwrite the outer signal handler.
Sequential calls—finishing one loop before starting the next—work correctly.

**State is a snapshot, not a history.**  `loopmonitor` stores only the most
recent state.  `ipc break` and `ipc checkpoint` save the state at the moment
the command is issued; they do not replay the full per-iteration history.  For
complete metric logging, write values to a file inside the loop or combine
`loopmonitor` with a dedicated experiment-tracking tool.

**`ipc plot` requires a display.**  The matplotlib window opens in the
process's display environment (`$DISPLAY` on Linux, the macOS window server on
macOS).  Running over SSH without X forwarding will raise a matplotlib backend
error.

**`ipc pause` / `ipc resume` do not work in interactive Python sessions.**
Sending SIGSTOP to a REPL that is the foreground job of a terminal causes the
shell to reclaim that terminal; SIGCONT then resumes the process in the
background.  `ipc pause` prints a warning when it detects this situation.  For
interactive sessions, use Ctrl-Z and `fg` in the session's own terminal.

# AI usage disclosure

The package was designed and implemented by the author. Claude Sonnet 4.6
(via Claude Code) was used to improve and test the Python and R packages,
write detailed documentation, and eliminate security vulnerabilities.
It was also used to improve this paper.

# References
