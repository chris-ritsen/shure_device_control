#!/usr/bin/env python3

import argparse
import asyncio
from datetime import datetime
from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import Header, Footer, Static, ProgressBar, Label
from textual.reactive import reactive
from textual.timer import Timer

from p10t import send_command as p10t_send_command
from ad4d import send_command as ad4d_send_command


class VolumeDisplay(Static):
    def __init__(self, channel: int, device_type: str = "p10t"):
        super().__init__()
        self.channel = channel
        self.device_type = device_type
        self.left_level = 0
        self.right_level = 0
        self.peak_left = 0
        self.peak_right = 0
        self.last_update = "Never"

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(f"Channel {self.channel}", classes="channel-title")
            with Horizontal():
                yield Label("L:", classes="level-label")
                yield ProgressBar(
                    total=32767,
                    show_eta=False,
                    show_percentage=False,
                    id=f"left-{self.channel}",
                )
                yield Label("0", id=f"left-value-{self.channel}", classes="level-value")
            with Horizontal():
                yield Label("R:", classes="level-label")
                yield ProgressBar(
                    total=32767,
                    show_eta=False,
                    show_percentage=False,
                    id=f"right-{self.channel}",
                )
                yield Label(
                    "0", id=f"right-value-{self.channel}", classes="level-value"
                )
            yield Label(
                f"Peak: L:{self.peak_left} R:{self.peak_right}",
                id=f"peak-{self.channel}",
                classes="peak-info",
            )
            yield Label(
                f"Updated: {self.last_update}",
                id=f"updated-{self.channel}",
                classes="update-info",
            )

    def update_levels(self, left: int, right: int):
        self.left_level = left
        self.right_level = right
        self.peak_left = max(self.peak_left, left)
        self.peak_right = max(self.peak_right, right)
        self.last_update = datetime.now().strftime("%H:%M:%S")

        left_bar = self.query_one(f"#left-{self.channel}", ProgressBar)
        right_bar = self.query_one(f"#right-{self.channel}", ProgressBar)
        left_bar.update(progress=left)
        right_bar.update(progress=right)

        self.query_one(f"#left-value-{self.channel}", Label).update(str(left))
        self.query_one(f"#right-value-{self.channel}", Label).update(str(right))
        self.query_one(f"#peak-{self.channel}", Label).update(
            f"Peak: L:{self.peak_left} R:{self.peak_right}"
        )
        self.query_one(f"#updated-{self.channel}", Label).update(
            f"Updated: {self.last_update}"
        )

    def reset_peaks(self):
        self.peak_left = 0
        self.peak_right = 0


class VolumeMonitorApp(App):
    CSS = """
    .channel-title {
        text-align: center;
        text-style: bold;
        margin: 1 0;
    }
    
    .level-label {
        width: 3;
        text-align: right;
        margin-right: 1;
    }
    
    .level-value {
        width: 8;
        text-align: right;
        margin-left: 1;
    }
    
    .peak-info {
        text-align: center;
        color: $accent;
        margin-top: 1;
    }
    
    .update-info {
        text-align: center;
        color: $text-muted;
        text-style: italic;
    }
    
    VolumeDisplay {
        border: solid $primary;
        margin: 1;
        padding: 1;
    }
    
    ProgressBar {
        margin: 0 1;
    }
    """

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("r", "reset_peaks", "Reset Peaks"),
        ("p", "toggle_pause", "Pause/Resume"),
    ]

    def __init__(
        self,
        host: str,
        port: int = 2202,
        device_type: str = "p10t",
        channels: list = None,
    ):
        super().__init__()
        self.host = host
        self.port = port
        self.device_type = device_type
        self.channels = channels or ([1, 2] if device_type == "p10t" else [1, 2, 3, 4])
        self.paused = False
        self.timer = None

    def compose(self) -> ComposeResult:
        yield Header()
        with Container(id="main"):
            for channel in self.channels:
                yield VolumeDisplay(channel, self.device_type)
        yield Footer()

    def on_mount(self) -> None:
        self.timer = self.set_interval(0.1, self.update_levels)

    async def update_levels(self) -> None:
        if self.paused:
            return

        print(f"[DEBUG] Updating levels for {len(self.channels)} channels")
        try:
            for channel in self.channels:
                if self.device_type == "p10t":
                    left_cmd = f"GET {channel} AUDIO_IN_LVL_L"
                    right_cmd = f"GET {channel} AUDIO_IN_LVL_R"

                    print(f"[DEBUG] Sending P10T commands: {left_cmd}, {right_cmd}")
                    left_val = p10t_send_command(
                        self.host,
                        self.port,
                        command=left_cmd,
                        expect_key=f"{channel} AUDIO_IN_LVL_L",
                        output_format="text",
                    )
                    right_val = p10t_send_command(
                        self.host,
                        self.port,
                        command=right_cmd,
                        expect_key=f"{channel} AUDIO_IN_LVL_R",
                        output_format="text",
                    )
                    print(f"[DEBUG] Got values: L={left_val}, R={right_val}")
                else:
                    left_cmd = f"GET {channel} AUDIO_LEVEL_PEAK"
                    right_cmd = f"GET {channel} AUDIO_LEVEL_RMS"

                    left_val = ad4d_send_command(
                        self.host,
                        self.port,
                        command=left_cmd,
                        expect_key=f"{channel} AUDIO_LEVEL_PEAK",
                        output_format="text",
                    )
                    right_val = ad4d_send_command(
                        self.host,
                        self.port,
                        command=right_cmd,
                        expect_key=f"{channel} AUDIO_LEVEL_RMS",
                        output_format="text",
                    )

                try:
                    left_level = (
                        int(left_val) if left_val and left_val != "(no match)" else 0
                    )
                    right_level = (
                        int(right_val) if right_val and right_val != "(no match)" else 0
                    )
                except (ValueError, TypeError):
                    left_level = right_level = 0

                volume_display = (
                    self.query(VolumeDisplay)
                    .filter(lambda w: w.channel == channel)
                    .first()
                )
                if volume_display:
                    volume_display.update_levels(left_level, right_level)

        except Exception as e:
            self.title = f"Error: {e}"

    def action_reset_peaks(self) -> None:
        for volume_display in self.query(VolumeDisplay):
            volume_display.reset_peaks()

    def action_toggle_pause(self) -> None:
        self.paused = not self.paused
        status = "PAUSED" if self.paused else "MONITORING"
        self.sub_title = status


def main():
    parser = argparse.ArgumentParser(
        description="Live volume level monitor for Shure devices"
    )
    parser.add_argument("--host", required=True, help="Device IP or hostname")
    parser.add_argument(
        "--port", type=int, default=2202, help="Device port (default: 2202)"
    )
    parser.add_argument(
        "--device", choices=["p10t", "ad4d"], default="p10t", help="Device type"
    )
    parser.add_argument(
        "--channels", nargs="+", type=int, help="Channels to monitor (default: all)"
    )
    args = parser.parse_args()

    if not args.channels:
        args.channels = [1, 2] if args.device == "p10t" else [1, 2, 3, 4]

    app = VolumeMonitorApp(args.host, args.port, args.device, args.channels)
    app.title = f"Volume Monitor - {args.device.upper()} @ {args.host}"
    app.sub_title = "MONITORING"
    app.run()


if __name__ == "__main__":
    main()
