def audio_playering_app_name():
    from pycaw.pycaw import AudioUtilities, IAudioMeterInformation

    sessions = AudioUtilities.GetAllSessions()
    for session in sessions:
        if session.Process:
            process_name = session.Process.name()
            meter = session._ctl.QueryInterface(IAudioMeterInformation)
            peak_value = meter.GetPeakValue()
            if peak_value > 0:  # 如果峰值电平大于 0，表示正在播放音频
                # print(f"Process Name: {process_name}, Peak Value: {peak_value}")
                return process_name
    else:
        return None


def pause_other_audio():
    import keyboard

    keyboard.send("play/pause")


if __name__ == "__main__":
    import time

    while True:
        if process_name := audio_playering_app_name():
            print(f"Audio is currently playering by {process_name} .")
            pause_other_audio()
        else:
            print("No audio is playering.")
        time.sleep(1)
