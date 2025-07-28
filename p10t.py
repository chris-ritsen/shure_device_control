#!/usr/bin/env python3

import argparse
import socket
import time
import sys
import json
import pprint

DEVICE_GET_ONLY_KEYS = ["DEVICE_NAME"]
DEVICE_GET_SET_KEYS = []

CHANNEL_GET_SET_KEYS = [
    "CHAN_NAME",
    "AUDIO_IN_LVL",
    "GROUP_CHAN",
    "FREQUENCY",
    "RF_TX_LVL",
    "RF_MUTE",
    "AUDIO_TX_MODE",
    "AUDIO_IN_LINE_LVL",
    "METER_RATE",
]

ALL_KEYS = DEVICE_GET_ONLY_KEYS + DEVICE_GET_SET_KEYS + CHANNEL_GET_SET_KEYS


def format_output(data, output_format):
    if output_format == "json":
        sorted_data = {}
        if isinstance(data, dict):
            str_keys = sorted([k for k in data.keys() if isinstance(k, str)])
            int_keys = sorted([k for k in data.keys() if isinstance(k, int)])

            for k in str_keys + int_keys:
                sorted_data[k] = data[k]
        else:
            sorted_data = data
        return json.dumps(sorted_data, indent=2)
    elif output_format == "pretty":
        return pprint.pformat(data, indent=2, width=80, sort_dicts=True)
    elif output_format == "raw":
        return str(data)
    else:  # text
        if isinstance(data, dict):
            if not data:
                return "(no data)"
            lines = []
            device_keys = sorted([k for k in data.keys() if not str(k).isdigit()])
            channel_keys = sorted([k for k in data.keys() if str(k).isdigit()], key=int)

            for key in device_keys + channel_keys:
                value = data[key]
                if isinstance(value, dict):
                    lines.append(f"Channel {key}:")
                    for k, v in sorted(value.items()):
                        lines.append(f"  {k}: {v}")
                else:
                    lines.append(f"{key}: {value}")
            return "\n".join(lines)
        return str(data)


def build_command(channel, key):
    key = key.upper()

    if key not in ALL_KEYS:
        raise ValueError(f"Unknown key: {key}")

    if key in DEVICE_GET_ONLY_KEYS or key in DEVICE_GET_SET_KEYS:
        return key

    if channel not in ("1", "2"):
        raise ValueError("Channel must be 1 or 2 for channel-level keys")

    return f"{channel} {key}"


def parse_report_line(line):
    parts = line.strip("<> ").split()

    if len(parts) < 3 or parts[0] != "REPORT":
        return None

    if parts[1] in ("1", "2"):
        return {
            "channel": int(parts[1]),
            parts[2]: parts[3] if len(parts) > 3 else None,
        }

    return {"channel": None, parts[1]: parts[2] if len(parts) > 2 else None}


def send_command(
    host, port, command=None, expect_key=None, output_format="text", bulk=False
):
    try:
        with socket.create_connection((host, port), timeout=2) as sock:
            sock.settimeout(2)

            if bulk:
                cmds = [f"GET {k}" for k in DEVICE_GET_ONLY_KEYS]

                for ch in ("1", "2"):
                    cmds.extend(f"GET {ch} {k}" for k in CHANNEL_GET_SET_KEYS)

                for cmd in cmds:
                    sock.sendall(f"< {cmd} >\r\n".encode("utf-8"))
                    time.sleep(0.01)

                time.sleep(0.4)
            else:
                sock.sendall(f"< {command} >\r\n".encode("utf-8"))
                time.sleep(0.1)

            try:
                chunks = []
                end = time.time() + 0.5

                while time.time() < end:
                    try:
                        chunk = sock.recv(4096)

                        if not chunk:
                            break
                        chunks.append(chunk)
                    except socket.timeout:
                        break

                raw = b"".join(chunks).decode("utf-8", errors="ignore")

                lines = [
                    f"< REPORT {part.strip()}"
                    for part in raw.split("< REPORT ")
                    if part.strip()
                ]

                if bulk:
                    result = []

                    for line in lines:
                        parsed = parse_report_line(line)

                        if parsed:
                            result.append(parsed)

                    merged = {}

                    for entry in result:
                        ch = entry.pop("channel")

                        if ch is None:
                            merged.update(entry)
                        else:
                            if ch not in merged:
                                merged[ch] = {}

                            merged[ch].update(entry)
                    return format_output(merged, output_format)

                for line in lines:
                    parsed = parse_report_line(line)

                    if not parsed:
                        continue

                    if parsed["channel"] is not None:
                        match_key = f"{parsed['channel']} {next(k for k in parsed if k != 'channel')}"
                    else:
                        match_key = next(k for k in parsed if k != "channel")

                    if match_key == expect_key:
                        if bulk or output_format != "text":
                            return format_output(parsed, output_format)
                        else:
                            return next(v for k, v in parsed.items() if k != "channel")
                return (
                    format_output({}, output_format)
                    if bulk or output_format != "text"
                    else "(no match)"
                )
            except socket.timeout:
                return "(timeout)"
    except Exception as e:
        return f"(error: {e})"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", help="P10T IP or hostname")
    parser.add_argument("--port", type=int, default=2202)
    parser.add_argument("--get", action="store_true")
    parser.add_argument("--set", action="store_true")
    parser.add_argument(
        "--channel", help="Channel number (1 or 2), required for channel keys"
    )
    parser.add_argument("--key", help="Key to get or set (see --list)")
    parser.add_argument("--value", help="Value to set (required with --set)")
    parser.add_argument(
        "--output-format",
        choices=["text", "json", "pretty", "raw"],
        default="text",
        help="Output format: text (default), json, pretty, or raw",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON (deprecated, use --output-format=json)",
    )
    parser.add_argument(
        "--list", action="store_true", help="List all known keys with permissions"
    )
    args = parser.parse_args()

    if args.list:
        print("Device-level keys:")
        for k in DEVICE_GET_ONLY_KEYS:
            print(f"  {k}  [read-only]")
        for k in DEVICE_GET_SET_KEYS:
            print(f"  {k}  [read/write]")
        print("\nChannel-level keys (require --channel 1 or 2):")
        for k in CHANNEL_GET_SET_KEYS:
            print(f"  {k}  [read/write]")
        sys.exit(0)

    if not args.host:
        print("Error: --host is required", file=sys.stderr)
        sys.exit(1)

    output_format = "json" if args.json else args.output_format

    if args.get and not args.key:
        if args.channel:
            result = {}
            for key in CHANNEL_GET_SET_KEYS:
                try:
                    full_key = build_command(args.channel, key)
                    value = send_command(
                        args.host,
                        args.port,
                        f"GET {full_key}",
                        expect_key=full_key,
                        output_format="text",
                    )
                    if value and value != "(no match)":
                        result[key] = value
                except:
                    pass
            print(format_output({int(args.channel): result}, output_format))
        else:
            print(
                send_command(
                    args.host, args.port, output_format=output_format, bulk=True
                )
            )
        sys.exit(0)

    try:
        full_key = build_command(args.channel, args.key)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    if args.set:
        if args.key.upper() in DEVICE_GET_ONLY_KEYS:
            print(
                f"Error: {args.key.upper()} is read-only and cannot be set",
                file=sys.stderr,
            )
            sys.exit(1)
        if args.value is None:
            print("Error: --value is required for --set", file=sys.stderr)
            sys.exit(1)
        result = send_command(
            args.host,
            args.port,
            f"SET {full_key} {args.value}",
            expect_key=full_key,
            output_format=output_format,
        )
        if result:
            print(result)

    elif args.get:
        print(
            send_command(
                args.host,
                args.port,
                f"GET {full_key}",
                expect_key=full_key,
                output_format=output_format,
            )
        )

    else:
        print("Error: must specify --get or --set", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
