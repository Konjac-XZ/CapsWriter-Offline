from __future__ import annotations

import cProfile
import importlib
import pstats
import time
from dataclasses import dataclass
from pathlib import Path

from src.gui.runtime import ROOT


@dataclass
class StartupProfileOptions:
    enabled: bool = False
    tool: str = "cprofile"
    output: Path | None = None
    duration_ms: int = 5000


def _default_profile_output(tool: str) -> Path:
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    profile_dir = ROOT / "profiles" / "startup"
    profile_dir.mkdir(parents=True, exist_ok=True)
    return profile_dir / f"startup_{tool}_{timestamp}"


class StartupProfiler:
    """Optional startup profiler with multiple backends and safe fallback."""

    def __init__(self, options: StartupProfileOptions | None = None):
        self.options = options or StartupProfileOptions()
        self.enabled = bool(self.options.enabled)
        self.tool = (self.options.tool or "cprofile").strip().lower()
        self.output_base = self.options.output or _default_profile_output(self.tool)
        self.output_base.parent.mkdir(parents=True, exist_ok=True)
        self._started_at: float | None = None
        self._cprofile: cProfile.Profile | None = None
        self._pyinstrument = None
        self._viztracer = None
        self._yappi = None

    def start(self) -> None:
        if not self.enabled:
            return
        self._started_at = time.perf_counter()
        try:
            if self.tool == "cprofile":
                self._cprofile = cProfile.Profile()
                self._cprofile.enable()
                print(
                    f"[startup-profiler] cProfile started -> {self.output_base.with_suffix('.pstats')}"
                )
                return

            if self.tool == "pyinstrument":
                Profiler = importlib.import_module("pyinstrument").Profiler
                self._pyinstrument = Profiler(async_mode="disabled")
                self._pyinstrument.start()
                print(
                    f"[startup-profiler] pyinstrument started -> {self.output_base.with_suffix('.html')}"
                )
                return

            if self.tool == "viztracer":
                VizTracer = importlib.import_module("viztracer").VizTracer
                self._viztracer = VizTracer(
                    output_file=str(self.output_base.with_suffix(".json"))
                )
                self._viztracer.start()
                print(
                    f"[startup-profiler] viztracer started -> {self.output_base.with_suffix('.json')}"
                )
                return

            if self.tool == "yappi":
                self._yappi = importlib.import_module("yappi")
                self._yappi.clear_stats()
                self._yappi.set_clock_type("wall")
                self._yappi.start()
                print(
                    f"[startup-profiler] yappi started -> {self.output_base.with_suffix('.pstat')}"
                )
                return

            print(
                f"[startup-profiler] unknown tool '{self.tool}', fallback to cprofile"
            )
            self.tool = "cprofile"
            self._cprofile = cProfile.Profile()
            self._cprofile.enable()
        except Exception as e:
            print(
                f"[startup-profiler] failed to start '{self.tool}': {e}; fallback to cprofile"
            )
            self.tool = "cprofile"
            self._cprofile = cProfile.Profile()
            self._cprofile.enable()

    def stop(self, reason: str = "") -> None:
        if not self.enabled:
            return

        elapsed_ms = None
        if self._started_at is not None:
            elapsed_ms = int((time.perf_counter() - self._started_at) * 1000)

        try:
            if self.tool == "cprofile" and self._cprofile is not None:
                pstats_path = self.output_base.with_suffix(".pstats")
                summary_path = self.output_base.with_suffix(".txt")
                self._cprofile.disable()
                self._cprofile.dump_stats(str(pstats_path))

                with summary_path.open("w", encoding="utf-8") as handle:
                    stats = pstats.Stats(self._cprofile, stream=handle)
                    stats.sort_stats("cumulative")
                    stats.print_stats(100)

                print(f"[startup-profiler] cProfile saved: {pstats_path}")
                print(f"[startup-profiler] cProfile summary: {summary_path}")

            elif self.tool == "pyinstrument" and self._pyinstrument is not None:
                txt_path = self.output_base.with_suffix(".txt")
                html_path = self.output_base.with_suffix(".html")
                self._pyinstrument.stop()

                txt_path.write_text(
                    self._pyinstrument.output_text(unicode=True, color=False),
                    encoding="utf-8",
                )
                html_path.write_text(self._pyinstrument.output_html(), encoding="utf-8")

                print(f"[startup-profiler] pyinstrument text: {txt_path}")
                print(f"[startup-profiler] pyinstrument html: {html_path}")

            elif self.tool == "viztracer" and self._viztracer is not None:
                json_path = self.output_base.with_suffix(".json")
                self._viztracer.stop()
                self._viztracer.save()
                print(f"[startup-profiler] viztracer trace: {json_path}")

            elif self.tool == "yappi" and self._yappi is not None:
                pstat_path = self.output_base.with_suffix(".pstat")
                self._yappi.stop()
                stats = self._yappi.get_func_stats()
                stats.save(str(pstat_path), type="pstat")
                print(f"[startup-profiler] yappi stats: {pstat_path}")

            if elapsed_ms is not None:
                reason_str = f" ({reason})" if reason else ""
                print(f"[startup-profiler] captured ~{elapsed_ms}ms{reason_str}")
        except Exception as e:
            print(f"[startup-profiler] failed to save profile output: {e}")
        finally:
            self.enabled = False
