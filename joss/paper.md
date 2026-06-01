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
cleanly, inject a new parameter value, or save a snapshot from outside the
running process.

`loopmonitor` fills this gap with a *minimal instrumentation, maximal control*
design.  The target audience is anyone who runs long iterative computations in
Python or R on a local workstation or HPC cluster: statisticians running MCMC
chains, machine-learning practitioners tuning neural networks, and researchers
conducting large-scale simulation studies.

# Implementation

**Python backend.**  When `ipc_range()` starts, it creates a named FIFO in
`~/.ipc/` with owner-only permissions (mode `0600`) and installs a `SIGUSR1`
handler.  The CLI writes a command string to the FIFO and signals the process;
the handler wakes, reads all queued commands atomically, and dispatches them.
Per-iteration overhead is limited to a single JSON state-file write (tens of
microseconds), negligible for the MCMC steps and mini-batch updates that
`loopmonitor` targets.  A persistent write-end file descriptor prevents
spurious end-of-file between CLI invocations, following standard POSIX
practice [@stevens2013].

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

# Usage

A Python training loop is instrumented with one import and one keyword change:

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
Full documentation for each package is available in the
[Python README](https://github.com/haimbar/loopmonitor#readme) and the
[R README](https://github.com/haimbar/loopmonitor-r#readme).


# References
