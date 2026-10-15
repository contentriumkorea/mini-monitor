"""Bounded synthetic desktop benchmark; no USB, login, or user files touched.

Run in a fresh process, optionally with --baseline-ref HEAD to compare a revision.
Results exclude the hardware/.NET collector and Codex account subprocess.
"""
import argparse
import importlib
import json
from pathlib import Path
import subprocess
import sys
import time
import tkinter as tk

import psutil


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--baseline-ref')
    parser.add_argument('--seconds', type=float, default=10)
    args = parser.parse_args()
    root_dir = Path(__file__).resolve().parents[1]
    if args.baseline_ref:
        for name in ('controller', 'ui.overlay', 'ui.tray', 'ui.setup'):
            module = importlib.import_module('ai_mini_monitor.' + name)
            path = 'src/ai_mini_monitor/' + name.replace('.', '/') + '.py'
            source = subprocess.check_output(['git', 'show', args.baseline_ref + ':' + path], cwd=root_dir)
            exec(compile(source, str(root_dir / path), 'exec'), module.__dict__)
    sys.path.insert(0, str(root_dir / 'tests'))
    from test_controller import FakeCollector
    from ai_mini_monitor.config import AppConfig
    from ai_mini_monitor.controller import MonitorController
    from ai_mini_monitor.ui.overlay import OverlayWindow, WorkArea
    from ai_mini_monitor.ui.layered_window import LayeredWindowPresenter

    class CountingPresenter(LayeredWindowPresenter):
        uploads = 0
        def present(self, *args, **kwargs):
            super().present(*args, **kwargs)
            CountingPresenter.uploads += 1

    root = tk.Tk()
    root.withdraw()
    overlay = OverlayWindow(root, position=(-31000, -31000),
        work_areas_provider=lambda _: (WorkArea(-32000, -32000, -28000, -28000),),
        presenter_factory=CountingPresenter)
    controller = MonitorController(AppConfig(), enable_serial=False, sensor_collector_factory=FakeCollector)
    process = psutil.Process()
    controller.start()
    overlay.show(notify=False)
    def tick():
        frame = controller.latest_image()
        if frame is not None:
            overlay.update_image(frame)
        root.update()
    try:
        until = time.monotonic() + 2
        while time.monotonic() < until:
            tick()
            time.sleep(.1)
        cpu_start = sum(process.cpu_times()[:2])
        start = time.monotonic()
        frame_start = controller.stats_snapshot()['render_frames']
        upload_start = CountingPresenter.uploads
        peak = process.memory_info().rss
        while time.monotonic() - start < args.seconds:
            tick()
            peak = max(peak, process.memory_info().rss)
            time.sleep(.1)
        elapsed = time.monotonic() - start
        print(json.dumps(dict(mode=args.baseline_ref or 'working-tree', seconds=round(elapsed, 2),
            cpu_one_core_percent=round(100 * (sum(process.cpu_times()[:2]) - cpu_start) / elapsed, 2),
            working_set_peak_mib=round(peak / 2**20, 2),
            private_mib=round(process.memory_info().private / 2**20, 2),
            rendered=controller.stats_snapshot()['render_frames'] - frame_start,
            native_uploads=CountingPresenter.uploads - upload_start)))
    finally:
        controller.stop()
        overlay.destroy()
        root.destroy()


if __name__ == '__main__':
    main()
