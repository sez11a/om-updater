---
name: gui-threading
description: Keep PyQt6 GUI code off the GUI thread.
  Use when adding, editing, or reviewing any PyQt6 code in this repo
  (om-updater.py, om-installer.py), or when a Qt app freezes, deadlocks,
  or aborts (SIGABRT) inside a slot or during a click.
---

# GUI threading (PyQt6 / Qt6)

PyQt6 runs one event loop on the main (GUI) thread. Anything that blocks that
thread — a `subprocess.run` with a network timeout, a `select`/`poll`, a
synchronous libdnf5 resolve, a spin loop — stalls the UI and, on the GUI
thread, can deadlock, re-enter Qt, or trip an assertion. Python 3.14 + PyQt6
makes the abort worse, not milder. Keep work off that thread.

## The one rule

Do not call a blocking function from a Qt slot, from a
`QApplication.processEvents()` call, or anywhere the GUI event loop is
running. Blocking means: `subprocess.run`, `time.sleep`, network I/O,
`dnf`/`libdnf5` resolves, `rpm`/`flatpak`/`snap` lookups, file scans, or
anything that can wait. `QTimer.timeout`, button `clicked`,
`itemSelectionChanged`, `readyReadStandardOutput` are all slots.

## Pattern: move the work to a worker

- A `QThread` subclass (`run()` does the blocking work and emits signals), or a
  `QProcess` for external commands, does the work on its own thread.
- The GUI slot only (a) shows a non-blocking placeholder, (b) starts the worker,
  and (c) reacts to the worker's signals. No `.run()` calls, no
  `capture_output=True` waits, no `wait()` on the GUI thread.
- Signals cross the thread boundary automatically. Handlers run on the GUI
  thread, so it is safe to touch widgets inside them.

`om-installer.py` has the working shape: `SearchWorker(QThread)` (search off
thread), `QProcess` (install/remove via `pkexec`), a `placeholder` row, and
`_on_search_finished` / `_on_worker_output` handlers. Reuse it.

## In-flight / cancellation safety

- A search or install started earlier may still be running when the user acts
  again (switch category, click a package, start a new install). Guard it.
- Tag results so you can drop stale ones: check the request still matches the
  current state (e.g. same category) before applying it. Cheap and race-free.
- Do not `wait()` on the GUI thread to cancel an old worker. `QThread.wait()`
  blocks; calling it from a slot is the same class of bug. Let the old worker
  finish and clean itself up via the built-in `QThread.finished` signal, or
  use `QProcess.kill()` for a hard stop.

## Qt signal naming

Do not declare a custom signal named `finished`, `started`, or `error` on a
`QObject`/`QThread` subclass. `QThread` already defines `finished` and
`started`; overriding them surprises you and breaks `QThread` mechanics.
Name custom signals distinctively (e.g. `search_done`, `search_failed_sig`).

## Main-loop hygiene (in `main()`)

- Do not call `QApplication.processEvents()` manually, and do not wire a
  recurring `QTimer` to `QApplication.processEvents`. The event loop is already
  running; hand-pumping it re-enters Qt and invites the exact deadlocks and
  asserts this skill is about. Let `app.exec()` own the loop.
- Route SIGINT through the loop: a 0-ms single-shot `QTimer` whose `timeout`
  calls `app.quit()`. Starting that timer from the signal handler avoids
  quitting from inside a blocked `exec()`. See `om-installer.py:main()`.

## Widgets and data

- Create `QApplication` before any `QWidget`.
- `blockSignals(True)` while populating models in init so early emission cannot
  reach unready state.
- Add each `QTreeWidgetItem` once. A parented constructor already adds it;
  an extra `addTopLevelItem(...)` duplicates the row.
- Only touch Qt objects from the GUI thread (inside slots and signal handlers).

## Before you ship

- `python3 -m py_compile <file>` for syntax.
- Search the touched file for `subprocess.run`, `.wait(`, `processEvents`,
  `time.sleep`, and custom signals named `finished`/`started`/`error`. Each hit
  must be justified off the GUI thread or removed.
- You should be able to reproduce the path: the exact click or keystroke that
  used to abort, and confirm it now shows a placeholder and returns via a
  signal.
