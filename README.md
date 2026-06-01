# loopmonitor

**On-demand status queries and graceful loop control for long-running Python programs.**

`loopmonitor` lets you inspect or steer a running Python program from a second terminal — without modifying the program while it runs, restarting it, or connecting to a cloud service. You add one line to your loop; everything else is controlled from the command line.

---

## Table of contents

1. [Installation](#installation)
2. [The core idea](#the-core-idea)
3. [Instrumenting your code](#instrumenting-your-code)
   - [Basic usage](#basic-usage)
   - [Tracking values](#tracking-values)
   - [Wrapping an existing iterable](#wrapping-an-existing-iterable)
   - [Monitoring while loops](#monitoring-while-loops)
   - [State update frequency](#state-update-frequency)
4. [The `ipc` command-line tool](#the-ipc-command-line-tool)
   - [`ipc list`](#ipc-list)
   - [`ipc peek`](#ipc-peek)
   - [`ipc plot`](#ipc-plot)
   - [`ipc continue`](#ipc-continue)
   - [`ipc break`](#ipc-break)
   - [`ipc set`](#ipc-set)
   - [`ipc pause` / `ipc resume`](#ipc-pause--ipc-resume)
   - [`ipc tail`](#ipc-tail)
   - [`ipc notify`](#ipc-notify)
   - [`ipc checkpoint`](#ipc-checkpoint)
   - [`ipc stack`](#ipc-stack)
   - [`ipc memory`](#ipc-memory)
   - [`ipc clean`](#ipc-clean)
5. [How it works internally](#how-it-works-internally)
6. [Security](#security)
7. [Worked examples](#worked-examples)
   - [Long training loop](#long-training-loop)
   - [MCMC sampler](#mcmc-sampler)
   - [Trace plot with windowed view](#trace-plot-with-windowed-view)
   - [Grid search](#grid-search)
8. [Comparison with TensorBoard, W&B, and tqdm](#comparison-with-tensorboard-wb-and-tqdm)
9. [Limitations](#limitations)

---

## Installation

```bash
pip install loopmonitor
```

`loopmonitor` requires Python 3.9 or later and runs on **Linux and macOS** (any POSIX system that supports named FIFOs and `SIGUSR1`). Native Windows is not supported, but it works on Windows via WSL — see [Limitations](#limitations).

The only required dependency is **matplotlib** (used by `ipc plot`). Everything else is standard library.

---

## The core idea

When you run a long loop — training a model, running an MCMC chain, processing a large dataset — you often want to know:

- *How far along is it? How much time is left?*
- *Is the loss actually decreasing, or has it diverged?*
- *I have a meeting in five minutes — can I stop the loop cleanly and keep the results so far?*

The standard approach is to add `print` statements and restart. `loopmonitor` lets you ask those questions **after the program is already running**, from a separate terminal, without touching the code again.

You instrument your loop once:

```python
from loopmonitor import ipc_range

for step in ipc_range(10_000, label="training"):
    loss = train_one_step()
    step.track(loss=loss)
```

Then, while it runs, you use the `ipc` command:

```
ipc peek 12345             # print current iteration, elapsed time, loss
ipc plot 12345             # pop up a matplotlib window showing tracked values
ipc continue 12345         # exit the loop cleanly, keep going with the rest of the script
ipc break 12345            # stop the program now, save state to a JSON file
ipc set 12345 lr=0.0001    # inject a new value that the loop can read via step.get()
ipc pause 12345            # suspend the process (SIGSTOP)
ipc resume 12345           # resume a suspended process (SIGCONT)
ipc tail 12345             # stream live status every 2 s in your terminal
ipc notify 12345 "loss < 0.1"  # get a desktop notification when a condition is met
ipc checkpoint 12345       # save a JSON snapshot without stopping the loop
ipc stack 12345            # print the call stack of the running process
ipc memory 12345           # print memory (RSS) usage of the process
```

---

## Instrumenting your code

### Basic usage

Replace `range(n)` with `ipc_range(n)`:

```python
from loopmonitor import ipc_range

for step in ipc_range(50_000, label="MCMC chain"):
    # ... your computation ...
    pass
```

`label` is what appears in `ipc list`. It defaults to the script name if omitted.

`step` is an `IPCStep` object. You can ignore it entirely if you only need timing and progress — but see [Tracking values](#tracking-values) for more.

### Tracking values

Call `step.track(**kwargs)` anywhere inside the loop body to record the current values of variables you care about. These values are shown by `ipc peek` and plotted by `ipc plot`.

```python
from loopmonitor import ipc_range

for step in ipc_range(10_000, label="training"):
    loss = compute_loss()
    acc  = compute_accuracy()
    step.track(loss=loss, accuracy=acc)
```

You can call `step.track()` multiple times in a single iteration — values accumulate. Only the most recent value for each key is stored:

```python
for step in ipc_range(1000, label="simulation"):
    x = update_position()
    step.track(x=x)

    energy = compute_energy(x)
    step.track(energy=energy)   # adds to the same snapshot
```

Tracked values can be scalars (`float`, `int`) or sequences (`list`, `tuple`). When you pass a sequence, `ipc plot` draws it as a line chart; scalars are displayed as large text.

### Wrapping an existing iterable

`ipc_range` accepts any iterable, not just integers:

```python
from loopmonitor import ipc_range

dataset = load_batches("train.h5")          # any iterable

for step in ipc_range(dataset, label="epoch 1"):
    loss = model.train_on_batch(step.index)
    step.track(loss=loss)
```

If the iterable has a `__len__`, the total is determined automatically and ETA is computed. For generators and other length-less iterables, total and ETA show `?`.

### Monitoring while loops

`ipc_range` is a drop-in for `range()`, but with one import you can wrap any `while` loop as well.

**The recommended approach: `itertools.count()`**

`itertools.count()` is an infinite iterator. Pass it to `ipc_range` and use a regular `break` where the `while` condition would go:

```python
import itertools
from loopmonitor import ipc_range

# Original while loop:
#   while not converged:
#       loss = train_step()
#       converged = loss < 1e-4

for step in ipc_range(itertools.count(), label="training"):
    loss = train_step()
    step.track(loss=loss)
    if loss < 1e-4:      # termination condition — same as the while check
        break
```

This is the cleanest option when the termination condition depends on values computed inside the loop body (which is the common case). All `ipc` commands work normally: `ipc continue` stops the loop early, `ipc break` stops the program, and `ipc peek`/`ipc tail` show progress. Because the total is unknown, ETA shows `?`.

**Alternative: a generator function**

If the termination condition is self-contained — for example, draining a queue or consuming a data source — you can express it as a generator and wrap that:

```python
from loopmonitor import ipc_range

def batches(loader):
    """Yield batches until the loader is exhausted."""
    while True:
        batch = loader.next_batch()
        if batch is None:
            return
        yield batch

for step in ipc_range(batches(my_loader), label="processing"):
    process(step.index)   # or track items via an external reference
    step.track(processed=step.index + 1)
```

The generator approach works well when the "while" logic belongs to the data source itself. It is less natural when the exit condition depends on variables updated inside the for loop body, because those variables are not in scope inside the generator — you would need a shared mutable object to communicate state back, which is more complex than a simple `break`.

---

### State update frequency

By default the state file is updated every iteration. For very fast inner loops (microsecond iterations) the file writes add overhead. Use `state_every` to write only every *n* iterations:

```python
for step in ipc_range(10_000_000, label="fast simulation", state_every=500):
    ...
```

The tradeoff is that `ipc peek` may show state that is up to `state_every` iterations stale. The default of `1` is fine for loops that take at least a few milliseconds per iteration.

---

## The `ipc` command-line tool

All commands follow the form:

```
ipc <command> [pid]
```

PIDs are shown by `ipc list`. You do not need to look them up yourself.

---

### `ipc list`

List all currently registered processes.

```
$ ipc list
     PID  ALIVE  LABEL                           STARTED
----------------------------------------------------------------------
   12345    yes  training                         2026-05-16 09:14:02
   12391    yes  MCMC chain                       2026-05-16 09:17:45
```

Columns:

| Column | Meaning |
|--------|---------|
| PID | Operating system process ID |
| ALIVE | Whether the process is still running (`os.kill(pid, 0)` check) |
| LABEL | The string passed as `label=` to `ipc_range` |
| STARTED | UTC timestamp when the loop started |

Processes deregister themselves automatically when the loop exits. If a process crashed without cleaning up, `ipc clean` removes the stale entry.

---

### `ipc peek`

Print the current status of a running process to your terminal.

```
$ ipc peek 12345
[loopmonitor] PID 12345  iter 3421/10000  (34.2%)
         elapsed 08:37  ETA 16:35
         loss=0.3847  accuracy=0.8821
```

Fields:

| Field | Meaning |
|-------|---------|
| `iter N/total` | Current iteration and total (if known) |
| `(pct%)` | Percentage complete |
| `elapsed` | Wall-clock time since the loop started, formatted as `MM:SS` or `H:MM:SS` |
| `ETA` | Estimated time remaining, computed from average iteration speed |
| tracked keys | Every key/value passed to `step.track()` so far |

The output appears in the terminal running your program, not in the terminal where you typed `ipc peek`. This is by design — the program prints to its own stdout, just as if you had put a `print` call inside the loop.

**Tip:** Run `ipc peek` multiple times to watch values change, or combine with `watch`:

```bash
watch -n 5 ipc peek 12345
```

---

### `ipc plot`

Display a matplotlib snapshot of the current tracked values.

```
$ ipc plot 12345
$ ipc plot 12345 --last 500    # show only the last 500 steps of each trace
```

A window opens in the program's display showing:

- One subplot per tracked variable
- **Scalars** are shown as a large centred label (e.g. `loss = 0.3847`)
- **Sequences** (lists or tuples) are drawn as line plots — useful when you accumulate a history of values in the loop

The loop keeps running while the window is open.

**`--last K` — windowed view**

When a tracked variable is a sequence, `--last K` restricts the plot to the most recent `K` elements. Pass `--last 0` (the default) to show the entire history. This is especially useful for long MCMC chains or training runs where the early iterations are no longer of interest and the full plot is too compressed to read.

```
$ ipc plot 12345 --last 200    # zoom in on the last 200 steps
$ ipc plot 12345 --last 0      # show all data (default)
```

**Example — tracking a sequence:**

```python
history = []

for step in ipc_range(5000, label="loss curve"):
    loss = train_step()
    history.append(loss)
    step.track(loss_history=history)   # pass the whole list each iteration
```

When you run `ipc plot 12345`, you get a line chart of the loss from iteration 0 to the current iteration. Run `ipc plot 12345 --last 100` to zoom in on the most recent 100 steps.

---

### `ipc continue`

Tell the loop to stop iterating but let the rest of the program continue.

```
$ ipc continue 12345
[loopmonitor] 'continue' sent to PID 12345.
```

The running program prints:

```
[loopmonitor] 'continue' received — loop will exit after this iteration.
```

The loop stops yielding after the **current iteration completes**. Any code after the `for` loop runs normally. This is equivalent to a clean `break` that you inject from the outside.

**Use case:** You are running a training loop and the model has clearly converged. You want to stop training and proceed to evaluation without restarting the script.

```python
from loopmonitor import ipc_range

for step in ipc_range(50_000, label="pretraining"):
    train_step()
    step.track(loss=loss)

# This runs even after ipc continue — the loop just exits early
evaluate_model()
save_checkpoint("final.pt")
```

---

### `ipc break`

Stop the program immediately (after the current iteration), print the current state, and save a JSON snapshot.

```
$ ipc break 12345
[loopmonitor] 'break' sent to PID 12345.
```

The running program prints:

```
[loopmonitor] PID 12345  iter 3421/10000  (34.2%)
         elapsed 08:37  ETA 16:35
         loss=0.3847  accuracy=0.8821
[loopmonitor] Stopping — state saved.
[loopmonitor] State written to loopmonitor_break_12345_20260516T091437.json
```

The JSON file is written in the **current working directory of the program** at the time of the break:

```json
{
  "pid": 12345,
  "iteration": 3421,
  "total": 10000,
  "elapsed_sec": 517.3,
  "eta_sec": 995.6,
  "tracked": {
    "loss": 0.3847,
    "accuracy": 0.8821
  },
  "updated": "2026-05-16T09:14:37.214501+00:00"
}
```

**Use case:** The loss has exploded and you want to stop immediately to diagnose the problem, keeping the tracked state for inspection.

**Difference from `ipc continue`:**

| | `ipc continue` | `ipc break` |
|---|---|---|
| Loop exits? | Yes | Yes |
| Code after loop runs? | **Yes** | **No** — calls `sys.exit(0)` |
| JSON snapshot saved? | No | Yes |
| Use when | Converged early, run eval | Diverged, crash, external interrupt |

---

### `ipc set`

Inject a value into the running loop without stopping it.

```
$ ipc set 12345 lr=0.0001
```

The value is delivered to the loop's `step.get()` method:

```python
from loopmonitor import ipc_range

for step in ipc_range(10_000, label="training"):
    # Read a value that may be injected at any time from outside
    lr = step.get("lr", default=0.01)
    optimizer.set_lr(lr)
    loss = train_step(lr=lr)
    step.track(loss=loss, lr=lr)
```

The value string is parsed with Python's `ast.literal_eval`, which accepts numbers, strings, booleans, lists, dicts, and tuples — but **not** arbitrary expressions. This prevents code injection via the FIFO.

```bash
ipc set 12345 lr=0.0001          # float
ipc set 12345 epochs=50          # int
ipc set 12345 tags="['a','b']"   # list
```

If `step.get("lr")` is called before any `ipc set lr=…` has been sent, it returns the specified default (or `None` if no default is given). Once set, the value persists for the rest of the loop unless overwritten by another `ipc set`.

---

### `ipc pause` / `ipc resume`

Suspend or resume a process without stopping the loop.

```bash
$ ipc pause 12345
[loopmonitor] PID 12345 paused (SIGSTOP).

$ ipc resume 12345
[loopmonitor] PID 12345 resumed (SIGCONT).
```

`ipc pause` sends `SIGSTOP` to the process, which suspends it at the OS level — the process is frozen in place and uses no CPU. `ipc resume` sends `SIGCONT` to wake it up exactly where it left off.

**Use cases:**
- Free a CPU/GPU for a short urgent task without losing your training state.
- Inspect memory usage of the frozen process with external tools.
- Coordinate multiple loops on the same machine by pausing all but one.

> **Note:** The process is frozen at the OS scheduler level and does not save any state. The loop resumes from exactly where it was paused. If your process holds locks, network connections, or open files that may time out, resume promptly.

> **Interactive sessions:** `ipc pause` does not work correctly when the target is an interactive Python session (a `python3` REPL running in a terminal). Sending SIGSTOP to a terminal's foreground process causes the shell to reclaim the terminal; SIGCONT then resumes the process in the background, but the interactive prompt never returns. `ipc pause` will print a warning if it detects this situation. For interactive sessions, use **Ctrl+Z** in the session's terminal to suspend and **`fg`** to resume.

---

### `ipc tail`

Stream live status updates to your terminal at a regular interval.

```
$ ipc tail 12345
[loopmonitor] Tailing PID 12345 every 2.0s — Ctrl+C to stop.
[loopmonitor] PID 12345  iter 1420/10000  (14.2%)  elapsed 02:22  ETA 14:12  loss=0.4132
[loopmonitor] PID 12345  iter 1638/10000  (16.4%)  elapsed 02:44  ETA 13:57  loss=0.3981
[loopmonitor] PID 12345  iter 1855/10000  (18.6%)  elapsed 03:05  ETA 13:34  loss=0.3847
…
```

Stop with Ctrl+C. The tail stops automatically when the process exits.

Options:

```
$ ipc tail 12345 --interval 5   # poll every 5 seconds (default: 2)
```

Unlike `watch -n 5 ipc peek 12345`, `ipc tail` reads the state file directly without signalling the process, so it adds no overhead to the running loop.

---

### `ipc notify`

Watch a tracked value and send a desktop notification when a condition becomes true.

```bash
$ ipc notify 12345 "loss < 0.05"
[loopmonitor] Watching PID 12345 for: 'loss < 0.05'  (every 5.0s — Ctrl+C to stop)
…
[loopmonitor] Condition met — notification sent.
```

When the condition is satisfied, a system notification is sent (macOS Notification Center or Linux `notify-send`) and `ipc notify` exits.

The condition is a Python expression evaluated against the tracked values. You can also use `iteration`, `total`, and `elapsed` (seconds):

```bash
ipc notify 12345 "loss < 0.1"
ipc notify 12345 "accuracy > 0.95"
ipc notify 12345 "iteration > 5000"
ipc notify 12345 "elapsed > 3600"      # alert after 1 hour
ipc notify 12345 --interval 10 "loss < 0.2"
```

The expression is evaluated with `__builtins__` removed, so only the tracked variables and the fields above are in scope. Arbitrary Python calls are not available.

> **Linux note:** `ipc notify` requires `notify-send` to be installed (`sudo apt install libnotify-bin`).

---

### `ipc checkpoint`

Save a JSON snapshot of the current state without stopping the loop.

```
$ ipc checkpoint 12345
[loopmonitor] 'checkpoint' sent to PID 12345.
```

The running program prints:

```
[loopmonitor] Checkpoint saved to loopmonitor_checkpoint_12345_20260516T143022.json
```

The snapshot has the same structure as the `ipc break` JSON file:

```json
{
  "pid": 12345,
  "iteration": 3421,
  "total": 10000,
  "elapsed_sec": 517.3,
  "tracked": { "loss": 0.3847 },
  "updated": "2026-05-16T14:30:22.000000+00:00"
}
```

Use `ipc checkpoint` periodically as a lightweight backup when `ipc break` would be too disruptive. The loop continues uninterrupted.

---

### `ipc stack`

Print the Python call stack of the running process to its stdout.

```
$ ipc stack 12345
[loopmonitor] 'stack' sent to PID 12345 — output appears in the process terminal.
```

The process prints something like:

```
[loopmonitor] Stack trace for PID 12345:
  File "train.py", line 22, in <module>
    for step in ipc_range(10_000, label="training"):
  File "./loopmonitor/range.py", line 87, in __iter__
    yield step
  File "train.py", line 24, in <module>
    loss = model.train_step()
  File "model.py", line 88, in train_step
    return self._forward(batch)
```

Useful for diagnosing a loop that seems stuck or slower than expected — you can see exactly which call is taking time without attaching a debugger.

---

### `ipc memory`

Print the resident set size (RSS) memory usage of the running process.

```
$ ipc memory 12345
[loopmonitor] 'memory' sent to PID 12345 — output appears in the process terminal.
```

The process prints:

```
[loopmonitor] PID 12345 memory — RSS: 4231.8 MB
```

Useful for spotting memory leaks mid-run. Combine with repeated `ipc memory` calls or `ipc tail` to watch memory grow over time.

---

### `ipc clean`

Remove stale entries from the registry (processes that have exited without deregistering, e.g. after a crash).

```
$ ipc clean
Removed stale entries: [12345, 12391]
```

```
$ ipc clean
Registry is clean.
```

Processes that exit normally (loop completes, `ipc continue`, or `ipc break`) deregister themselves. You only need `ipc clean` after abnormal termination such as `kill -9`, an unhandled exception, or a power failure.

---

## How it works internally

```
Your program                              Your second terminal
────────────────────────────────────      ────────────────────
ipc_range() starts                        $ ipc peek 12345
  │                                           │
  ├─ creates ~/.ipc/12345.fifo               ├─ opens ~/.ipc/12345.fifo for writing
  ├─ registers in ~/.ipc/registry.json       ├─ writes "peek\n"  (atomic: ≤ PIPE_BUF)
  ├─ installs SIGUSR1 handler                └─ sends SIGUSR1 to PID 12345
  │                                                    │
  │   [loop running]          ◄────────────── SIGUSR1 delivered
  │       │
  │   signal handler fires
  │       ├─ reads "peek\n" from FIFO
  │       └─ prints status to stdout
  │
  │   [loop continues]
```

**Why a named FIFO instead of a file?**

POSIX guarantees that writes of ≤ `PIPE_BUF` bytes to a pipe are **atomic** — no partial writes, no torn reads. Because all commands ("peek", "plot", "continue", "break") are well under this limit, the CLI can write without holding a lock. The pipe buffer also acts as a natural queue: two rapid commands both land safely without overwriting each other. A single `SIGUSR1` can deliver multiple queued commands because the handler reads all available bytes at once.

The FIFO is created with permissions `0o600` (owner read/write only) and `~/.ipc/` with `0o700` (owner only). See the [Security](#security) section for the full threat model and the protections built into the CLI.

The registry and per-process state files (`~/.ipc/<pid>.state.json`) are plain JSON, human-readable, and can be inspected directly if needed.

---

## Security

`loopmonitor` is designed for single-user use: the person who starts the program is the same person who sends it commands. The protections described here defend against interference from *other users* on a shared machine (HPC login nodes, shared workstations).

### What loopmonitor does not protect against

- **Root.** A process running as root can signal any process, read any file, and replace any FIFO regardless of permissions.
- **The same user.** Any process running as you can write to your FIFOs. If you are the only user on the machine, or you trust all processes running under your account, there is nothing further to configure.
- **Kernel exploits.** Out of scope for a user-space tool.

If you are the only user of your machine or cluster account, you can skip the rest of this section.

### Threat model on shared machines

On a multi-user system without the protections below, two attacks are plausible:

**Unauthorized process control.** Another user who can write to `~/.ipc/<pid>.fifo` can send `break` to kill your training run (and read your tracked metrics from the JSON it saves), or `continue` to abort your loop early.

**Symlink substitution (TOCTOU).** An attacker who can write to `~/.ipc/` can delete the FIFO and replace it with a symlink. When the `ipc` CLI opens the path for writing, it would actually write to the symlink's target, potentially corrupting another file.

### Protections built into loopmonitor

Three independent layers are applied:

**1. Restrictive filesystem permissions**

| Path | Mode | Effect |
|------|------|--------|
| `~/.ipc/` | `0o700` | Other users cannot list, read, or enter the directory |
| `~/.ipc/<pid>.fifo` | `0o600` | Other users cannot open the FIFO for reading or writing |
| `~/.ipc/<pid>.state.json` | inherits from `~/.ipc/` | Not reachable by other users |

Even on a system with a permissive umask, these modes are set explicitly at creation time.

**2. Symlink-safe open in the CLI**

The CLI opens the FIFO with `O_NOFOLLOW`:

```python
fd = os.open(path, os.O_WRONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
```

`O_NOFOLLOW` causes the `open()` call to fail immediately with `OSError` if the final path component is a symbolic link, regardless of where the symlink points. An attacker cannot substitute the FIFO with a symlink and trick the CLI into writing to an arbitrary file.

**3. Post-open verification**

After the `open()` succeeds, the CLI verifies the open file descriptor before writing anything to it:

```python
st = os.fstat(fd)                          # stat the already-open fd, not the path

if not stat.S_ISFIFO(st.st_mode):          # must be a FIFO, not a regular file or device
    raise OSError("not a FIFO")

if st.st_uid != os.getuid():               # must be owned by the current user
    raise OSError("wrong owner")
```

Using `fstat` (on the fd) rather than `stat` or `lstat` (on the path) eliminates any remaining TOCTOU window: the file being checked is exactly the file that was opened.

### Note on command injection

The FIFO carries plain text commands (`peek`, `plot`, `continue`, `break`, `set key=value`, …). The server-side handler dispatches these through a strict allowlist of string comparisons. The only command that carries user-supplied data is `set`, which parses its value argument with Python's `ast.literal_eval`. This function accepts only literal values (numbers, strings, booleans, lists, dicts, tuples) and raises `ValueError` for anything else — including calls to `__import__`, attribute access, or function calls. There is no `eval()` with builtins, no `exec()`, and no subprocess invocation that receives FIFO content, so writing arbitrary bytes to the FIFO cannot cause arbitrary code execution.

---

## Worked examples

### Long training loop

```python
# train.py
import time
from loopmonitor import ipc_range

def train_step(i, lr):
    time.sleep(0.1)                  # simulate GPU work
    return 1.0 / (1 + i * lr)

for step in ipc_range(1000, label="ResNet training"):
    lr = step.get("lr", default=0.01)   # can be updated live via ipc set
    loss = train_step(step.index, lr)
    step.track(loss=round(loss, 4), lr=lr)
```

While it runs, in another terminal:

```bash
# See what's running
$ ipc list
     PID  ALIVE  LABEL                           STARTED
----------------------------------------------------------------------
   44201    yes  ResNet training                  2026-05-16 14:00:01

# Check progress
$ ipc peek 44201
[loopmonitor] PID 44201  iter 142/1000  (14.2%)
         elapsed 00:14  ETA 01:25
         loss=0.4132  lr=0.01

# Stream live updates (Ctrl+C to stop)
$ ipc tail 44201
[loopmonitor] Tailing PID 44201 every 2.0s — Ctrl+C to stop.
[loopmonitor] PID 44201  iter 200/1000  elapsed 00:20  ETA 01:20  loss=0.3981  lr=0.01
[loopmonitor] PID 44201  iter 247/1000  elapsed 00:24  ETA 01:13  loss=0.3847  lr=0.01
^C

# Reduce learning rate on the fly
$ ipc set 44201 lr=0.001

# Show a plot
$ ipc plot 44201

# Watch for convergence in a second terminal
$ ipc notify 44201 "loss < 0.05"
[loopmonitor] Watching PID 44201 for: 'loss < 0.05'  (every 5.0s — Ctrl+C to stop)
…
[loopmonitor] Condition met — notification sent.

# Satisfied it's converging — stop the loop and proceed to evaluation
$ ipc continue 44201
[loopmonitor] 'continue' sent to PID 44201.
```

The script output:

```
[loopmonitor] 'continue' received — loop will exit after this iteration.
```

---

### MCMC sampler

```python
# mcmc.py
import random
import time
from loopmonitor import ipc_range

def log_posterior(theta, data):
    return -0.5 * sum((y - theta) ** 2 for y in data)

data = [random.gauss(3.5, 1.0) for _ in range(200)]
chain = []
theta = 0.0

for step in ipc_range(500_000, label="MCMC chain"):
    proposal = theta + random.gauss(0, 0.3)
    if random.random() < min(1, 2.718 ** (log_posterior(proposal, data)
                                          - log_posterior(theta, data))):
        theta = proposal
    chain.append(theta)
    step.track(theta=round(theta, 4), chain_length=len(chain))

posterior_mean = sum(chain[50000:]) / len(chain[50000:])
print(f"Posterior mean: {posterior_mean:.4f}")
```

Check the sampler mid-run:

```bash
$ ipc peek 55310
[loopmonitor] PID 55310  iter 127843/500000  (25.6%)
         elapsed 03:12  ETA 09:17
         theta=3.4821  chain_length=127843

# The sampler looks stuck — request a plot of recent chain values
$ ipc plot 55310

# Decide it has mixed well enough — exit loop, compute posterior
$ ipc continue 55310
```

---

### Trace plot with windowed view

A *trace plot* shows the value of a sampled parameter at every iteration — the standard visual check for MCMC mixing. When the chain is long, showing all iterations at once compresses the recent behaviour into a narrow sliver. `--last K` lets you zoom in on the most recent `K` steps without stopping or restarting the run.

```python
# trace_mcmc.py
import math
import random
import itertools
from loopmonitor import ipc_range


def log_target(x):
    """Bimodal target: equal-weight mixture of N(-2, 1) and N(2, 1)."""
    return math.log(
        0.5 * math.exp(-0.5 * (x + 2) ** 2)
        + 0.5 * math.exp(-0.5 * (x - 2) ** 2)
        + 1e-300
    )


chain = []
x = 0.0

for step in ipc_range(itertools.count(), label="MCMC trace"):
    proposal = x + random.gauss(0, 1.0)
    log_alpha = log_target(proposal) - log_target(x)
    if math.log(random.random() + 1e-300) < log_alpha:
        x = proposal
    chain.append(round(x, 4))
    step.track(x=chain)          # pass the full list — ipc plot draws it as a trace
```

While it runs, from another terminal:

```bash
# Check progress
$ ipc peek 78123
[loopmonitor] PID 78123  iter 24801/?
         elapsed 00:08  ETA ?
         x=1.8732

# Show the full trace so far — all 24 801 steps
$ ipc plot 78123

# The chain looks compressed; zoom in on the last 500 steps to inspect mixing
$ ipc plot 78123 --last 500

# Zoom in further — last 100 steps
$ ipc plot 78123 --last 100

# Explicitly request all data (same as the default)
$ ipc plot 78123 --last 0

# Chain looks well mixed — stop the loop and continue to analysis
$ ipc continue 78123
```

The `--last 0` default is equivalent to passing the full length of the list — it always shows everything. Any positive `K` slices the list to its final `K` elements, and the x-axis label shows `step (last K of N)` so you know where in the chain the window sits.

> **Note:** `ipc plot` reads the state file written by the most recent `step.track()` call, so the list passed as `x=chain` must be the *entire accumulated history*, not just the latest value. Appending to a list and passing it each iteration (as above) is the standard pattern.

---

### Grid search

```python
# grid_search.py
import itertools
import time
from loopmonitor import ipc_range

param_grid = list(itertools.product(
    [0.001, 0.01, 0.1],       # learning rate
    [32, 64, 128],             # batch size
    [1e-4, 1e-3],              # weight decay
))

best_val_loss = float("inf")
best_params = None

for step in ipc_range(param_grid, label="grid search"):
    lr, bs, wd = param_grid[step.index]
    val_loss = run_experiment(lr, bs, wd)   # your function here

    if val_loss < best_val_loss:
        best_val_loss = val_loss
        best_params = (lr, bs, wd)

    step.track(
        val_loss=round(val_loss, 4),
        best_val_loss=round(best_val_loss, 4),
    )

print(f"Best params: {best_params}  val_loss={best_val_loss:.4f}")
```

Mid-search:

```bash
$ ipc peek 66102
[loopmonitor] PID 66102  iter 9/18  (50.0%)
         elapsed 01:34  ETA 01:34
         val_loss=0.2341  best_val_loss=0.1892

# Best loss has barely improved in the last 5 configs — stop early
$ ipc break 66102
```

Output in the program terminal:

```
[loopmonitor] PID 66102  iter 9/18  (50.0%)
         elapsed 01:34  ETA 01:34
         val_loss=0.2341  best_val_loss=0.1892
[loopmonitor] Stopping — state saved.
[loopmonitor] State written to loopmonitor_break_66102_20260516T152634.json
```

You can then inspect the JSON file to see exactly which parameters had been tested.

---

## Comparison with TensorBoard, W&B, and tqdm

| Feature | loopmonitor | TensorBoard | Weights & Biases | tqdm |
|---|---|---|---|---|
| **No setup / no account** | ✓ | ✓ | requires account | ✓ |
| **No cloud / all local** | ✓ | ✓ | ✗ (SaaS) | ✓ |
| **Works with any Python code** | ✓ | partial¹ | partial¹ | ✓ |
| **Works with R code** | planned | ✗ | ✗ | ✗ |
| **On-demand status query** | ✓ | ✗² | ✗² | ✗ |
| **Live streaming (`tail`)** | ✓ | ✗ | ✗ | ✗ |
| **Graceful loop exit (continue)** | ✓ | ✗ | ✗ | ✗ |
| **Graceful program stop (break)** | ✓ | ✗ | ✗ | ✗ |
| **Mid-run value injection (set)** | ✓ | ✗ | ✗ | ✗ |
| **Pause / resume process** | ✓ | ✗ | ✗ | ✗ |
| **Desktop notifications** | ✓ | ✗ | ✗ | ✗ |
| **Mid-run snapshots (checkpoint)** | ✓ | ✗ | ✗ | ✗ |
| **Call stack inspection** | ✓ | ✗ | ✗ | ✗ |
| **Memory usage** | ✓ | ✗ | ✗ | ✗ |
| **Persistent metric history** | ✗³ | ✓ | ✓ | ✗ |
| **Web UI** | ✗ | ✓ | ✓ | ✗ |
| **Hyperparameter tracking** | ✗ | ✓ | ✓ | ✗ |
| **Experiment comparison** | ✗ | ✓ | ✓ | ✗ |

¹ TensorBoard and W&B work best when you call their logging APIs at every step. Adding them to arbitrary code is possible but requires restructuring around their callback model.

² Both tools show *current* logged values in a browser, but you must have configured logging *before* the run. You cannot query a process that wasn't instrumented with their APIs. `ipc peek` queries any `ipc_range`-instrumented loop at any time.

³ `loopmonitor` stores only the most recent state snapshot. If you need a full history of every loss value, log to a file inside your loop or use TensorBoard/W&B.

**The key difference** is *external control*: `ipc continue` and `ipc break` let you steer a running program from a separate process. This is not available in any of the tools above. The signal-based design means the program does not need to poll a server or check a variable — the loop responds to the signal immediately after the current iteration.

---

## Limitations

**POSIX only.** `loopmonitor` uses `SIGUSR1` and named FIFOs. Both are POSIX features unavailable on native Windows. There is no Windows fallback. However, `loopmonitor` works on Windows through **WSL (Windows Subsystem for Linux)**: install it inside your WSL environment (`pip install loopmonitor`) and run both your script and the `ipc` CLI from WSL terminals. Note that `ipc plot` requires a display; on WSL 2 this works out of the box on recent Windows 11 builds (WSLg), but may need an X server such as VcXsrv on older setups.

**Shared machines.** Commands can only be sent by the user who owns the process. On a multi-user system (HPC login node, shared workstation), the FIFO and working directory are created with owner-only permissions so other users cannot interfere. See the [Security](#security) section for the full threat model.

**One `ipc_range` loop per process at a time.** If a script calls `ipc_range` twice sequentially that is fine — each loop registers and deregisters cleanly. But nesting two `ipc_range` loops (one inside the other) is not supported; the inner loop would overwrite the signal handler.

**Scalar-only tracked values for `ipc peek`.** `ipc peek` prints the most recent value for each tracked key. It does not print history. If you want to see a trend, use `ipc plot` with a list value, or run `ipc peek` multiple times.

**`ipc plot` requires a display.** The matplotlib window is opened in the process's display environment (the `$DISPLAY` variable on Linux, the macOS window server on macOS). Running over SSH without X forwarding will fail with a matplotlib backend error.

**`ipc break` does not resume.** `ipc break` calls `sys.exit(0)`. It does not serialize the full Python heap. If you need to resume a computation, implement your own checkpoint logic inside the loop.

**`ipc pause` / `ipc resume` do not work in interactive Python sessions.** When the monitored process is a `python3` REPL running as the foreground job of a terminal, sending SIGSTOP causes the shell to reclaim that terminal. SIGCONT then resumes the process in the background, but the interactive prompt never returns. `ipc pause` prints a warning when it detects this situation. Use **Ctrl+Z** and **`fg`** inside the session's terminal instead. This limitation does not affect scripts run with `python3 script.py`.
