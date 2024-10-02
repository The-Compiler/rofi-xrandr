import sys
from pathlib import Path
from enum import Enum
from dataclasses import dataclass
from typing import Any
import subprocess

INTERNAL = "eDP-1"
DP_PREFIX = "DP"


class Action(Enum):
    LEFT_OF = "left-of"
    ABOVE = "above"
    RIGHT_OF = "right-of"
    SAME_AS = "same-as"
    OFF = "off"


@dataclass
class ScreenConfig:
    action: Action
    mode: str


SCREENS = {
    "hdmi": "HDMI-1",
    "dp1": f"{DP_PREFIX}-1",
    "dp2": f"{DP_PREFIX}-2",
    "dp3": f"{DP_PREFIX}-3",
    "dp4": f"{DP_PREFIX}-4",
    "dp-dock-1": f"{DP_PREFIX}-1-1",
    "dp-dock-2": f"{DP_PREFIX}-1-2",
    "dp-dock-3": f"{DP_PREFIX}-1-3",
}

CONFIGS = {
    "left": ScreenConfig(Action.LEFT_OF, "auto"),
    "above": ScreenConfig(Action.ABOVE, "auto"),
    "left fullhd": ScreenConfig(Action.LEFT_OF, "1920x1080"),
    "right": ScreenConfig(Action.RIGHT_OF, "auto"),
    "same": ScreenConfig(Action.SAME_AS, "auto"),
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


def get_connected_screens() -> list[str]:
    try:
        result = subprocess.run(["xrandr"], capture_output=True, text=True, check=True)
        xrandr_output = result.stdout.splitlines()
        connected_screens = []
        for name, screen in SCREENS.items():
            if any(line.startswith(f"{screen} connected ") for line in xrandr_output):
                connected_screens.append(name)
        return connected_screens
    except subprocess.CalledProcessError as e:
        notify_user(f"Error checking connected screens: {e.stderr}")
        sys.exit(1)


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


def xrandr_command(commands: list[tuple[Any]]) -> None:
    """Helper method to execute xrandr commands."""
    args = ["xrandr"]
    for output, action, *options in commands:
        args.extend(["--output", output, f"--{action.value}", *options])
    run_subprocess(args)


def configure_internal_screen() -> None:
    commands = [(screen_name, Action.OFF) for screen_name in SCREENS.values()]
    xrandr_command(commands)


def configure_home_screen() -> None:
    commands = [
        (SCREENS["dp2"], Action.LEFT_OF, INTERNAL, "--auto"),
        (
            SCREENS["dp-dock-2"],
            Action.LEFT_OF,
            SCREENS["dp2"],
            "--auto",
            "--rotate",
            "right",
        ),
        (INTERNAL, Action.OFF),
    ]
    xrandr_command(commands)


def configure_present_screen(connected_screens: list[str]) -> None:
    config = select_option(list(CONFIGS.keys()), "config")
    if config is None:
        sys.exit(0)  # Exit silently if user aborted the operation

    dp_outputs = [
        output
        for output in SCREENS.values()
        if output.startswith(DP_PREFIX) and f"{output} connected" in connected_screens
    ]
    if not dp_outputs:
        sys.exit(1)

    dp_output = dp_outputs[0]

    if SCREENS["hdmi"] in connected_screens:
        proj_output = SCREENS["hdmi"]
    else:
        proj_output = next((output for output in dp_outputs if "-" in output), None)

    if proj_output:
        config_settings = CONFIGS[config]
        commands = [
            (
                dp_output,
                config_settings.action,
                INTERNAL,
                "--mode",
                config_settings.mode,
            ),
            (proj_output, Action.SAME_AS, dp_output, "--auto"),
        ]
        xrandr_command(commands)


def configure_other_screen(screen: str) -> None:
    config = select_option(list(CONFIGS.keys()), "config")
    if config is None:
        sys.exit(0)  # Exit silently if user aborted the operation

    config_settings = CONFIGS[config]
    commands = [
        (
            SCREENS[screen],
            config_settings.action,
            INTERNAL,
            "--mode",
            config_settings.mode,
        )
    ]
    xrandr_command(commands)


def apply_screen_configuration(screen: str, connected_screens: list[str]) -> None:
    try:
        if screen == "internal":
            configure_internal_screen()
        elif screen == "home":
            configure_home_screen()
        elif screen == "present":
            configure_present_screen(connected_screens)
        else:
            configure_other_screen(screen)
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
    connected_screens = get_connected_screens()

    available_screens = connected_screens + ["internal", "home", "present"]
    screen = select_option(available_screens, "screen")

    if screen is None:
        # Exit silently if user aborted the operation
        sys.exit(0)

    apply_screen_configuration(screen, connected_screens)
    update_hlwm()
    restore_wallpaper()
    set_notifications_paused(screen == "present")


if __name__ == "__main__":
    main()
