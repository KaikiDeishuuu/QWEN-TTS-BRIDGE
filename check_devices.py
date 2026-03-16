import pyaudio

def list_devices():
    p = pyaudio.PyAudio()
    print("Available audio devices:")
    device_count = p.get_device_count()
    if device_count == 0:
        print("No audio devices found!")
    for i in range(device_count):
        dev = p.get_device_info_by_index(i)
        print(f"Device {i}: {dev['name']} (Max Output Channels: {dev['maxOutputChannels']})")
    p.terminate()

if __name__ == "__main__":
    list_devices()
