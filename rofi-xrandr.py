import sys
from pathlib import Path
from enum import Enum
from dataclasses import dataclass
from typing import Iterator, Sequence
import subprocess

DP_PREFIX = "DP"


class Relation(Enum):
    LEFT_OF = "left-of"
    ABOVE = "above"
    RIGHT_OF = "right-of"
    SAME_AS = "same-as"


class XrandrArg(Enum):
    AUTO = "--auto"
    MODE = "--mode"
    OFF = "--off"
    ROTATE = "--rotate"


@dataclass
class ScreenConfig:
    relation: Relation
    mode: str


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


CONFIGS = {
    "left": ScreenConfig(Relation.LEFT_OF, "auto"),
    "above": ScreenConfig(Relation.ABOVE, "auto"),
    "left fullhd": ScreenConfig(Relation.LEFT_OF, "1920x1080"),
    "right": ScreenConfig(Relation.RIGHT_OF, "auto"),
    "same": ScreenConfig(Relation.SAME_AS, "auto"),
}


def run_subprocess(cmd: list[str]) -> str:
    try:
        result = subprocess.run(
            cmd, shell=False, text=True, capture_output=True, check=True
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        notify_user(f"Error running subprocess: {e.stderr}")
        sys.exit(1)


def get_connected_screens() -> Iterator[str]:
    try:
        proc = subprocess.run(["xrandr"], capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as e:
        notify_user(f"Error checking connected screens: {e.stderr}")
        sys.exit(1)

    for line in proc.stdout.splitlines():
        if line.startswith(" "):
            continue
        screen, state, *_ = line.split()
        if state == "connected":
            yield screen


def select_option(options: list[str], prompt: str) -> str | None:
    result = subprocess.run(
        ["rofi", "-dmenu", "-p", prompt, "-m", "-5"],
        input="\n".join(options),
        text=True,
        capture_output=True,
    )
    if result.returncode == 1:
        return None  # User aborted the operation
    elif result.returncode != 0:
        notify_user(f"Error selecting option: {result.stderr}")
        sys.exit(1)
    return result.stdout.strip()


def notify_user(message: str) -> None:
    subprocess.run(
        ["notify-send", "-u", "critical", "Screen Configuration Error", message]
    )

def xrandr_command(
    commands: Sequence[tuple[str | KnownScreen | Relation | XrandrArg, ...]],
) -> None:
    """Helper method to execute xrandr commands."""
    args = ["xrandr"]
    for output, *options in commands:
        args += ["--output", output]
        args += [
            opt.value if isinstance(opt, (Relation, KnownScreen, XrandrArg)) else opt
            for opt in options
        ]
    run_subprocess(args)


def configure_internal_screen(connected_screens: list[str]) -> None:
    """Turn off everything, only laptop screen."""
    commands = [(screen, XrandrArg.OFF) for screen in connected_screens]
    xrandr_command(commands)


def configure_home_screen() -> None:
    """[vertical DisplayPort] - [normal USB-C] - [laptop]"""
    commands = [
        (KnownScreen.DP2, Relation.LEFT_OF, KnownScreen.INTERNAL, XrandrArg.AUTO),
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


def configure_present_screen(connected_screens: list[str]) -> None:
    """[projector] == [external USB-C] - [laptop]"""
    config = select_option(list(CONFIGS.keys()), "config")
    if config is None:
        # User aborted the operation
        sys.exit(0)

    dp_outputs = [
        screen for screen in connected_screens if screen.startswith(DP_PREFIX)
    ]
    if not dp_outputs:
        sys.exit(1)

    dp_output = next((output for output in dp_outputs if output.count("-") == 1))

    if KnownScreen.HDMI.value in connected_screens:
        proj_output = KnownScreen.HDMI
    else:
        proj_output = next((output for output in dp_outputs if output.count("-") == 2))

    if proj_output:
        config_settings = CONFIGS[config]
        commands = [
            (
                dp_output,
                config_settings.relation,
                KnownScreen.INTERNAL,
                XrandrArg.MODE,
                config_settings.mode,
            ),
            (proj_output, Relation.SAME_AS, dp_output, XrandrArg.AUTO),
        ]
        xrandr_command(commands)


def configure_other_screen(selection: str) -> None:
    config = select_option(list(CONFIGS.keys()), "config")
    if config is None:
        sys.exit(0)  # Exit silently if user aborted the operation

    try:
        screen = KnownScreen(selection.upper())
    except ValueError:
        screen = selection

    config_settings = CONFIGS[config]
    commands = [
        (
            screen,
            config_settings.relation,
            KnownScreen.INTERNAL,
            XrandrArg.MODE,
            config_settings.mode,
        )
    ]
    xrandr_command(commands)


def apply_screen_configuration(selection: str, connected_screens: list[str]) -> None:
    try:
        if selection == "internal":
            configure_internal_screen(connected_screens)
        elif selection == "home":
            configure_home_screen()
        elif selection == "present":
            configure_present_screen(connected_screens)
        else:
            configure_other_screen(selection)
    except subprocess.CalledProcessError as e:
        notify_user(f"Error applying screen configuration: {e.stderr}")


def set_notifications_paused(paused: bool) -> None:
    command = "true" if paused else "false"
    run_subprocess(["dunstctl", "set-paused", command])


def update_hlwm() -> None:
    run_subprocess(["herbstclient", "detect_monitors"])
    run_subprocess(["herbstclient", "emit_hook", "quit_panel"])

    monitors = run_subprocess(["herbstclient", "list_monitors"]).splitlines()
    for monitor in monitors:
        monitor_id = monitor.split(":")[0]
        subprocess.Popen(["barpyrus", monitor_id])


def restore_wallpaper() -> None:
    fehbg_path = Path.home() / ".fehbg"
    if fehbg_path.is_file():
        run_subprocess([str(fehbg_path)])


def main() -> None:
    connected_screens = list(get_connected_screens())

    options = ["internal"]
    if connected_screens != [KnownScreen.INTERNAL.value]:
        options += ["home", "present", ""]
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
        sys.exit(0)

    apply_screen_configuration(screen, connected_screens)
    update_hlwm()
    restore_wallpaper()
    set_notifications_paused(screen == "present")


if __name__ == "__main__":
    main()
