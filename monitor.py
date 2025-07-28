#!/usr/bin/env python3

import argparse
import re
import signal
import socket
import threading
import time

import redis
from log import init_logging, log
from notifier import Notifier

shutdown_requested = threading.Event()

reply_channel_re = re.compile(r"< REP (\d+) (\S+)\s+(\S+) >")
reply_device_re = re.compile(r"< REP (\S+)\s+{([^}]*)} >")
report_re = re.compile(r"< REPORT (\S+)(?: (\S+))?(?: (.+))? >")
sample_re = re.compile(r"< SAMPLE (\d+) ALL (.+) >")

POLL_COMMANDS = [
    "GET DEVICE_NAME",
    "GET DEVICE_ID",
    "GET MODEL",
    "GET SERIAL_NUMBER",
    "GET FW_VER",
    "GET ENCRYPTION_MODE",
    "GET FIRMWARE_UPDATE_PROGRESS",
    "GET EVENT_LOG_STATUS",
    "GET NETWORK_IP_ADDR",
    "GET NETWORK_SUBNET_MASK",
    "GET NETWORK_MAC_ADDR",
    "GET NETWORK_GATEWAY",
    "GET DEVICE_NOTES",
    "GET LOCATION",
    "GET 1 CHAN_NAME",
    "GET 1 FREQUENCY",
    "GET 1 AUDIO_GAIN",
    "GET 1 AUDIO_MUTE",
    "GET 1 ANTENNA_STATUS",
    "GET 1 AUDIO_LEVEL_RMS",
    "GET 1 AUDIO_LEVEL_PEAK",
    "GET 1 TX_DEVICE_ID",
    "GET 1 TX_BATT_MINS",
    "GET 1 TX_BATT_CHARGE_PERCENT",
    "GET 1 TX_MODEL",
    "GET 1 TX_LOCK",
    "GET 1 TX_TALK_SWITCH",
    "GET 2 CHAN_NAME",
    "GET 2 FREQUENCY",
    "GET 2 AUDIO_GAIN",
    "GET 2 AUDIO_MUTE",
    "GET 2 ANTENNA_STATUS",
    "GET 2 AUDIO_LEVEL_RMS",
    "GET 2 AUDIO_LEVEL_PEAK",
    "GET 2 TX_DEVICE_ID",
    "GET 2 TX_BATT_MINS",
    "GET 2 TX_BATT_CHARGE_PERCENT",
    "GET 2 TX_MODEL",
    "GET 2 TX_LOCK",
    "GET 2 TX_TALK_SWITCH",
]

DEVICE_LEVEL_KEYS = {
    "DEVICE_NAME",
    "DEVICE_ID",
    "MODEL",
    "SERIAL_NUMBER",
    "FW_VER",
    "FIRMWARE_VERSION",
    "ENCRYPTION_MODE",
    "FIRMWARE_UPDATE_PROGRESS",
    "EVENT_LOG_STATUS",
    "NETWORK_IP_ADDR",
    "NETWORK_SUBNET_MASK",
    "NETWORK_MAC_ADDR",
    "NETWORK_GATEWAY",
    "DEVICE_NOTES",
    "LOCATION",
}


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", required=True, help="Device hostname or IP")
    parser.add_argument("--port", type=int, default=2202, help="TCP control port")
    parser.add_argument(
        "--device", choices=["p10t", "ad4d"], required=True, help="Device type"
    )
    parser.add_argument(
        "--interval", type=float, default=5.0, help="Polling interval for AD4D"
    )

    return parser.parse_args()


def handle_response(host, redis_client, line, device_type):
    channel_match = reply_channel_re.match(line)
    device_match = reply_device_re.match(line)

    if device_match:
        key = device_match.group(1).strip()
        value = device_match.group(2).strip()
        redis_key = f"{host}:device"
        channel = None
    elif channel_match:
        channel = channel_match.group(1).strip()
        key = channel_match.group(2).strip()
        value = channel_match.group(3).strip()
        redis_key = f"{host}:channel:{channel}"
    else:
        log(f"{host}: unparsed line: {line}", PRIORITY=4)
        return

    redis_client.hset(redis_key, key, value)

    fields = {
        "REDIS_KEY": redis_key,
        "SHURE_HOST": host,
        "SHURE_DEVICE": device_type,
        "SHURE_KEY": key,
        "SHURE_VALUE": value,
        "SHURE_METRIC": key.upper(),
    }

    if channel:
        fields["SHURE_CHANNEL"] = channel

    if key.startswith("AUDIO_IN_LVL_") or key.startswith("AUDIO_LEVEL_"):
        fields["SHURE_METRIC"] = "AUDIO_LEVEL"

        if key.endswith("_L"):
            fields["SHURE_SIDE"] = "L"
        elif key.endswith("_R"):
            fields["SHURE_SIDE"] = "R"

    log(f"{redis_key} {key} = {value}", **fields)


def handle_sample(host, redis_client, ch_num, raw, device_type):
    redis_key = f"{host}:channel:{ch_num}"
    parts = raw.split()

    keys = [
        "CHANNEL_QUALITY",
        "AUDIO_LED_BITMAP",
        "AUDIO_LEVEL_PEAK",
        "AUDIO_LEVEL_RMS",
        "ANTENNA_STATUS",
        "RSSI_LED_BITMAP_A",
        "RSSI_A",
        "RSSI_LED_BITMAP_B",
        "RSSI_B",
    ]

    for k, v in zip(keys, parts[: len(keys)]):
        redis_client.hset(redis_key, k, v)

        fields = {
            "REDIS_KEY": redis_key,
            "SHURE_HOST": host,
            "SHURE_DEVICE": device_type,
            "SHURE_CHANNEL": ch_num,
            "SHURE_KEY": k,
            "SHURE_VALUE": v,
            "SHURE_METRIC": k,
        }

        log(f"{redis_key} {k} = {v}", **fields)


def init_metering(file):
    file.write(b"< SET 1 METER_RATE 1000 >\n")
    file.write(b"< SET 2 METER_RATE 1000 >\n")
    file.flush()


def run_polling_monitor(sock, host, interval, device_type):
    redis_client = redis.Redis()
    file = sock.makefile("rwb", buffering=0)

    init_metering(file)
    time.sleep(0.2)

    while not shutdown_requested.is_set():
        for cmd in POLL_COMMANDS:
            if not poll_command(file, sock, host, cmd, redis_client, device_type):
                return

        time.sleep(interval)


def poll_command(file, sock, host, cmd, redis_client, device_type):
    try:
        file.write(f"< {cmd} >\n".encode("utf-8"))
        file.flush()
        time.sleep(0.05)
        raw = sock.recv(4096)
    except socket.timeout:
        return True
    except Exception as e:
        log(f"polling error: {e}", PRIORITY=4)
        return False

    process_raw_data(raw, host, redis_client, device_type)
    return True


def process_raw_data(raw, host, redis_client, device_type):
    for part in raw.decode("utf-8", errors="ignore").split("<"):
        part = part.strip()

        if not part:
            continue

        line = f"< {part}"

        if line.startswith("< SAMPLE"):
            match = sample_re.match(line)

            if match:
                handle_sample(host, redis_client, *match.groups(), device_type)

            return

        handle_response(host, redis_client, line, device_type)


def run_passive_monitor(sock, host, device_type):
    redis_client = redis.Redis()
    buffer = b""

    while not shutdown_requested.is_set():
        try:
            chunk = sock.recv(4096)

            if not chunk:
                break

            buffer += chunk
        except socket.timeout:
            continue
        except Exception as e:
            log(f"read error: {e}", PRIORITY=4)
            return

        while b"\n" in buffer:
            line, buffer = buffer.split(b"\n", 1)
            line = line.decode("utf-8", errors="ignore").strip()

            if line.startswith("< SAMPLE"):
                match = sample_re.match(line)

                if match:
                    handle_sample(host, redis_client, *match.groups(), device_type)
            else:
                match = report_re.match(line)

                if match:
                    handle_response(host, redis_client, *match.groups(), device_type)
                else:
                    log(f"{host}: unparsed line: {line}")


def main():
    args = parse_args()
    init_logging("shure_monitor")
    notifier = Notifier()

    def handle_signal(signum, _):
        log(f"caught signal {signum} ({signal.Signals(signum).name}), shutting down")
        shutdown_requested.set()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    notifier.ready()
    last_status = None

    while not shutdown_requested.is_set():
        try:
            sock = socket.create_connection((args.host, args.port), timeout=2)
            sock.settimeout(2.0)
            log(f"connected to {args.host}:{args.port}")

            if last_status != "connected":
                notifier.status("connected")
                last_status = "connected"

            if args.device == "ad4d":
                run_polling_monitor(sock, args.host, args.interval, args.device)
            else:
                run_passive_monitor(sock, args.host, args.device)

            sock.close()
        except (OSError, socket.error) as e:
            if last_status != "waiting":
                log(f"{args.host} unreachable: {e}", PRIORITY=3)
                notifier.status("waiting for device...")
                last_status = "waiting"

            time.sleep(5)
        except Exception as e:
            log(f"{args.host} monitor crashed: {e}", PRIORITY=3)
            time.sleep(5)

    notifier.stopping()
    log("shutdown complete")


if __name__ == "__main__":
    main()
