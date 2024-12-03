import os
import sys
import signal
import traceback
import contextlib
from pathlib import Path
from enum import Enum
from dataclasses import dataclass
from typing import Iterator, Sequence
import subprocess
import argparse

import pyudev
import psutil

DP_PREFIX = "DP"
PRESENT_MODE = "1920x1080"


class Error(Exception):
    pass


class Relation(Enum):
    LEFT_OF = "--left-of"
    ABOVE = "--above"
    RIGHT_OF = "--right-of"
    SAME_AS = "--same-as"


class XrandrArg(Enum):
    AUTO = "--auto"
    MODE = "--mode"
    OFF = "--off"
    ROTATE = "--rotate"


class KnownScreen(Enum):
    INTERNAL = "eDP-1"
    HDMI = "HDMI-1"
    DP1 = "DP-1"
    DP2 = "DP-2"
    DP3 = "DP-3"
    DP4 = "DP-4"
    DP_DOCK_1 = "DP-1-1"
    DP_DOCK_2 = "DP-1-2"
    DP_DOCK_3 = "DP-1-3"


XrandrArgType = str | KnownScreen | Relation | XrandrArg


@dataclass
class ScreenConfig:
    relation: Relation
    args: list[XrandrArgType]


CONFIGS = {
    "left": ScreenConfig(Relation.LEFT_OF, [XrandrArg.AUTO]),
    "above": ScreenConfig(Relation.ABOVE, [XrandrArg.AUTO]),
    "left fullhd": ScreenConfig(Relation.LEFT_OF, [XrandrArg.MODE, "1920x1080"]),
    "right": ScreenConfig(Relation.RIGHT_OF, [XrandrArg.AUTO]),
    "same": ScreenConfig(Relation.SAME_AS, [XrandrArg.AUTO]),
}


def run_subprocess(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            cmd, shell=False, text=True, capture_output=True, check=True
        )
    except subprocess.CalledProcessError as e:
        raise Error(f"Error running subprocess: {e.stderr}")


def get_connected_screens() -> Iterator[str]:
    try:
        proc = subprocess.run(["xrandr"], capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as e:
        raise Error(f"Error checking connected screens: {e.stderr}")

    for line in proc.stdout.splitlines():
        if line.startswith(" "):
            continue
        screen, state, *_ = line.split()
        if state == "connected":
            yield screen


def pidfile_path() -> Path:
    return Path(os.environ["XDG_RUNTIME_DIR"]) / "rofi-xrandr.pid"


def maybe_kill_rofi() -> None:
    path = pidfile_path()

    try:
        pid = int(path.read_text())
    except FileNotFoundError:
        return

    try:
        cmd = psutil.Process(pid).cmdline()[0]
    except psutil.NoSuchProcess:
        path.unlink(missing_ok=True)
        return

    if cmd == "rofi":
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass

    path.unlink(missing_ok=True)


@contextlib.contextmanager
def write_rofi_pidfile(pid: int) -> Iterator[None]:
    path = pidfile_path()
    # FIXME do we need to use O_EXCL?
    path.write_text(str(pid))
    yield
    path.unlink(missing_ok=True)


def select_option(options: list[str], prompt: str) -> str | None:
    maybe_kill_rofi()

    proc = subprocess.Popen(
        ["rofi", "-dmenu", "-p", prompt, "-m", "primary"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf-8",
    )

    with write_rofi_pidfile(proc.pid):
        stdout, stderr = proc.communicate(input="\n".join(options))
        returncode = proc.poll()

    if returncode in [1, -signal.SIGTERM]:
        return None  # User (or we) aborted the operation
    elif returncode != 0:
        raise Error(f"Error selecting option: rofi returned {returncode}\n{stderr}")
    return stdout.strip()


def notify_user(message: str) -> None:
    subprocess.run(
        ["notify-send", "-u", "critical", "Screen Configuration Error", message]
    )


def xrandr_arg_to_str(arg: XrandrArgType) -> str:
    if isinstance(arg, str):
        return arg
    return arg.value


def xrandr_command(commands: Sequence[tuple[XrandrArgType, ...]]) -> None:
    """Helper method to execute xrandr commands."""
    args = ["xrandr"]
    for output, *options in commands:
        args += ["--output", xrandr_arg_to_str(output)]
        args += [xrandr_arg_to_str(opt) for opt in options]
    proc = run_subprocess(args)
    if proc.stderr:  # but exit code 0
        notify_user(proc.stderr)


def configure_internal_screen(connected_screens: list[str]) -> bool:
    """Turn off everything, only laptop screen."""
    commands = [
        (screen, XrandrArg.OFF)
        for screen in connected_screens
        if screen != KnownScreen.INTERNAL.value
    ]
    xrandr_command(commands)
    return True


def configure_home_screen(present: bool = False) -> bool:
    """[vertical DisplayPort] - [normal USB-C] - [laptop]

    If present=True, use full HD instead of 4K for USB-C middle screen.
    """
    dp2_args = [XrandrArg.MODE, PRESENT_MODE] if present else [XrandrArg.AUTO]
    commands = [
        (KnownScreen.DP2, Relation.LEFT_OF, KnownScreen.INTERNAL, *dp2_args),
        (
            KnownScreen.DP_DOCK_2,
            Relation.LEFT_OF,
            KnownScreen.DP2,
            XrandrArg.AUTO,
            XrandrArg.ROTATE,
            "right",
        ),
        (KnownScreen.INTERNAL, XrandrArg.AUTO),
    ]
    xrandr_command(commands)
    return True


def configure_present_screen(connected_screens: list[str]) -> bool:
    """[projector] == [external USB-C] - [laptop]"""
    config = select_option(list(CONFIGS.keys()), "config")
    if config is None:
        return False

    dp_outputs = [
        screen for screen in connected_screens if screen.startswith(DP_PREFIX)
    ]
    has_hdmi = KnownScreen.HDMI.value in connected_screens

    if not dp_outputs:
        raise Error("No DisplayPort outputs found")
    elif len(dp_outputs) == 1 and has_hdmi:
        proj_output = KnownScreen.HDMI
        mirror_output = dp_outputs[0]
    elif len(dp_outputs) == 2 and not has_hdmi:
        proj_output, mirror_output = dp_outputs
    else:
        raise Error(f"Too many screens found: {', '.join(connected_screens)}")

    config_settings = CONFIGS[config]
    commands = [
        (
            mirror_output,
            config_settings.relation,
            KnownScreen.INTERNAL,
            XrandrArg.MODE,
            PRESENT_MODE,
        ),
        (proj_output, Relation.SAME_AS, mirror_output, XrandrArg.MODE, PRESENT_MODE),
    ]
    xrandr_command(commands)
    return True


def configure_other_screen(selection: str) -> bool:
    config = select_option(list(CONFIGS.keys()), "config")
    if config is None:
        return False

    try:
        screen = KnownScreen[selection.upper()]
    except ValueError:
        screen = selection

    config_settings = CONFIGS[config]
    commands = [
        (
            screen,
            config_settings.relation,
            KnownScreen.INTERNAL,
            *config_settings.args,
        )
    ]
    xrandr_command(commands)
    return True


def apply_screen_configuration(selection: str, connected_screens: list[str]) -> None:
    if selection == "internal":
        changed = configure_internal_screen(connected_screens)
    elif selection == "home":
        changed = configure_home_screen()
    elif selection == "home-present":
        changed = configure_home_screen(present=True)
    elif selection == "present":
        changed = configure_present_screen(connected_screens)
    else:
        changed = configure_other_screen(selection)

    if changed:
        update_hlwm()
        restore_wallpaper()
        set_presentation_mode(selection in ["present", "home-present"])


def set_presentation_mode(present: bool) -> None:
    dunst_paused = "true" if present else "false"
    run_subprocess(["dunstctl", "set-paused", dunst_paused])
    xset_screensaver = "off" if present else "default"
    run_subprocess(["xset", "s", xset_screensaver])


def update_hlwm() -> None:
    run_subprocess(["herbstclient", "detect_monitors"])
    run_subprocess(["herbstclient", "emit_hook", "quit_panel"])

    monitors = run_subprocess(["herbstclient", "list_monitors"]).stdout.splitlines()
    for monitor in monitors:
        monitor_id = monitor.split(":")[0]
        subprocess.Popen(["barpyrus", monitor_id])


def restore_wallpaper() -> None:
    fehbg_path = Path.home() / ".fehbg"
    if fehbg_path.is_file():
        run_subprocess([str(fehbg_path)])


def listen() -> None:
    context = pyudev.Context()
    monitor = pyudev.Monitor.from_netlink(context)
    monitor.filter_by(subsystem="drm")

    for _ in iter(monitor.poll, None):
        # TODO can we somehow find out whether a screen was connected or disconnected?
        try:
            connected_screens = list(get_connected_screens())
            print(f"Detected change, now connected: {connected_screens}")

            if connected_screens == [KnownScreen.INTERNAL.value]:
                maybe_kill_rofi()
                apply_screen_configuration("internal", connected_screens)
            else:
                # needs to run in background so that another change can kill
                # rofi.
                subprocess.Popen([sys.executable, __file__])
        except Error as e:
            traceback.print_exc()
            notify_user(str(e))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("-l", "--listen", action="store_true")
    return parser.parse_args()


def run() -> None:
    connected_screens = list(get_connected_screens())

    options = ["internal"]
    if connected_screens != [KnownScreen.INTERNAL.value]:
        options += ["home", "home-present", "present", ""]
    for screen in connected_screens:
        if screen == KnownScreen.INTERNAL.value:
            continue
        try:
            options.append(KnownScreen(screen).name.lower())
        except ValueError:
            options.append(screen)

    screen = select_option(options, "screen")

    if screen is None:
        # Exit silently if user aborted the operation
        return

    apply_screen_configuration(screen, connected_screens)


def main() -> None:
    args = parse_args()
    if args.listen:
        listen()
        return

    try:
        run()
    except Error as e:
        traceback.print_exc()
        notify_user(str(e))


if __name__ == "__main__":
    main()
