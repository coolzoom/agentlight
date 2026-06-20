import sys

import serial

from serial_device import DEFAULT_BAUD_RATE, detect_port, expected_device_id, open_serial

MENU_ITEMS = {
    "1": ("IDLE", "idle"),
    "2": ("THINKING", "thinking"),
    "3": ("BUSY", "busy"),
    "4": ("SUCCESS", "success"),
    "5": ("WAIT_CONFIRM", "wait_confirm"),
    "6": ("CONFIRM", "confirm"),
    "7": ("WAITING", "waiting"),
    "8": ("WAIT", "wait"),
    "9": ("ERROR", "error"),
}


def print_menu():
    print()
    print("ESP32-C3 AI Agent 状态灯控制")
    for key, (label, _) in MENU_ITEMS.items():
        print(f"{key} {label}")
    print("q 退出")


def main():
    device_id = expected_device_id()
    port = detect_port(DEFAULT_BAUD_RATE, device_id)
    if not port:
        print(f"未检测到设备 ID：{device_id}")
        sys.exit(1)

    try:
        ser = open_serial(port, DEFAULT_BAUD_RATE, settle_seconds=0.5)
    except Exception as exc:
        print(f"打开串口失败：{port} - {exc}")
        sys.exit(1)

    print(f"已连接 {port}（{device_id}），波特率 {DEFAULT_BAUD_RATE}")

    try:
        while True:
            print_menu()
            choice = input("请输入选项：").strip().lower()

            if choice == "q":
                print("已退出。")
                break

            if choice not in MENU_ITEMS:
                print("无效选项，请重新输入。")
                continue

            label, command = MENU_ITEMS[choice]
            ser.write((command + "\n").encode("utf-8"))
            ser.flush()
            print(f"已发送：{label}")
    finally:
        ser.close()


if __name__ == "__main__":
    main()
