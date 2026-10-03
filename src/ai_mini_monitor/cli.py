from __future__ import annotations

import argparse
import getpass
import json
import logging
import sys
import time
from pathlib import Path

from . import __version__
from .app import run_desktop
from .autostart import disable as disable_autostart
from .autostart import enable as enable_autostart
from .config import AppConfig, load_config, save_config
from .controller import MonitorController
from .diagnostics import collect_diagnostics
from .logging_setup import setup_logging, shutdown_logging
from .models import AIProviderKind
from .orientation import orientation_spec
from .preview import PREVIEW_STATES, render_all_previews, render_preview
from .resources import user_data_dir
from .security.dpapi import DPAPISecretStore
from .ui.preview import PreviewWindow


LOGGER = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="AI-Mini-Monitor",
        description="480x320 or 320x480 USB35INCHIPSV2 system monitor",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--config", type=Path, help="regular (non-secret) JSON configuration path")
    parser.add_argument("--preview", nargs="?", const="normal", choices=PREVIEW_STATES, help="render a clearly marked DEMO preview")
    parser.add_argument("--preview-output", type=Path, help="PNG path for --preview")
    parser.add_argument("--no-window", action="store_true", help="do not open the preview window")
    parser.add_argument("--render-previews", type=Path, metavar="DIR", help="render all required native-size preview states")
    parser.add_argument("--diagnose", nargs="?", const="-", metavar="JSON", help="read-only device/sensor diagnostic; '-' prints JSON")
    parser.add_argument("--set-openai-key", action="store_true", help="securely prompt and store an OpenAI Admin Key with user-scope DPAPI")
    parser.add_argument("--clear-openai-key", action="store_true", help="delete the DPAPI-protected OpenAI Admin Key")
    parser.add_argument("--enable-autostart", action="store_true", help="explicitly enable current-user startup")
    parser.add_argument("--disable-autostart", action="store_true", help="disable current-user startup")
    parser.add_argument("--minimized", action="store_true", help="start the saved monitor configuration in the tray without showing the setup window")
    parser.add_argument("--headless-run", type=float, metavar="SECONDS", help=argparse.SUPPRESS)
    parser.add_argument("--desktop-smoke", type=float, metavar="SECONDS", help=argparse.SUPPRESS)
    parser.add_argument("--no-serial", action="store_true", help="run without opening a serial port")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = load_config(args.config)
    except Exception as error:
        print(f"Configuration error: {error}", file=sys.stderr)
        return 2
    logger = setup_logging(config.app.log_level)
    try:
        if args.set_openai_key:
            return _set_openai_key(config, args.config)
        if args.clear_openai_key:
            removed = DPAPISecretStore().delete()
            print("OpenAI Admin Key removed." if removed else "No stored OpenAI Admin Key was found.")
            return 0
        if args.enable_autostart:
            command = enable_autostart()
            print(f"Autostart enabled for the current user: {command}")
            return 0
        if args.disable_autostart:
            print("Autostart disabled." if disable_autostart() else "Autostart was already disabled.")
            return 0
        if args.diagnose is not None:
            return _diagnose(config, args.diagnose)
        if args.render_previews is not None:
            paths = render_all_previews(args.render_previews)
            print(f"Rendered {len(paths)} previews to {args.render_previews.resolve()}")
            return 0
        if args.preview is not None:
            dimensions = orientation_spec(config.device.rotation).dimensions
            return _preview(
                args.preview,
                args.preview_output,
                args.no_window,
                dimensions=dimensions,
            )
        if args.headless_run is not None:
            return _headless(config, args.headless_run, no_serial=args.no_serial)
        return run_desktop(
            config,
            minimized=args.minimized,
            enable_serial=not args.no_serial,
            config_path=args.config,
            auto_exit_seconds=args.desktop_smoke,
        )
    except KeyboardInterrupt:
        return 130
    except Exception:
        LOGGER.exception("command failed")
        print("The command failed. See the redacted rotating log for details.", file=sys.stderr)
        return 1
    finally:
        shutdown_logging(logger)


def _set_openai_key(config: AppConfig, config_path: Path | None) -> int:
    first = getpass.getpass("OpenAI Admin Key (input hidden): ").strip()
    if not first:
        print("Empty key was not saved.", file=sys.stderr)
        return 2
    second = getpass.getpass("Confirm OpenAI Admin Key: ").strip()
    if first != second:
        print("Keys did not match; nothing was saved.", file=sys.stderr)
        return 2
    DPAPISecretStore().set(first)
    config.ai.provider = AIProviderKind.OPENAI_API.value
    save_config(config, config_path)
    print("OpenAI Admin Key was protected with Windows user-scope DPAPI. The key was not printed or logged.")
    return 0


def _diagnose(config: AppConfig, destination: str) -> int:
    report = collect_diagnostics(config)
    text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if destination == "-":
        sys.stdout.write(text)
    else:
        path = Path(destination)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        print(f"Diagnostic report written to {path.resolve()}")
    return 0


def _preview(
    state: str,
    output: Path | None,
    no_window: bool,
    *,
    dimensions: tuple[int, int],
) -> int:
    path = output or (user_data_dir() / "previews" / f"{state}.png")
    render_preview(state, path, dimensions=dimensions)
    width, height = dimensions
    print(f"Native {width}x{height} DEMO preview: {path.resolve()}")
    if no_window:
        return 0
    from PIL import Image

    window = PreviewWindow(title=f"AI Mini Monitor · DEMO · {state}")
    with Image.open(path) as image:
        window.update_image(image.copy())
    window.run()
    return 0


def _headless(config: AppConfig, seconds: float, *, no_serial: bool) -> int:
    if seconds <= 0:
        raise ValueError("--headless-run must be positive")
    controller = MonitorController(config, enable_serial=not no_serial)
    controller.start()
    try:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            time.sleep(min(0.2, max(0.0, deadline - time.monotonic())))
    finally:
        controller.stop()
    output = user_data_dir() / "last-headless-frame.png"
    controller.save_latest(output)
    print(json.dumps(controller.stats_snapshot(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
