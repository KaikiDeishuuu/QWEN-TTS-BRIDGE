import os
import asyncio
import logging
import wave
from tts_realtime_client import TTSRealtimeClient, SessionMode
import pyaudio

# 从环境变量或 .env 加载配置 (手动加载逻辑已在下方定义)
def load_env():
    if os.path.exists(".env"):
        with open(".env", "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    key, value = line.split("=", 1)
                    if key not in os.environ:
                        os.environ[key] = value.strip()

load_env()

# --- 配置项 ---
API_KEY = os.getenv("DASHSCOPE_API_KEY")
URL = os.getenv("TTS_URL", "wss://dashscope.aliyuncs.com/api-ws/v1/realtime?model=qwen3-tts-instruct-flash-realtime")
VOICE = os.getenv("TTS_VOICE", "Maia")
INSTRUCTIONS = os.getenv("TTS_INSTRUCTIONS", "语速平稳，吐字清晰，语气温柔亲切。")

if not API_KEY:
    raise ValueError("请在 .env 文件中设置 DASHSCOPE_API_KEY 或配置系统环境变量")

# 收集音频数据
_audio_chunks = []
_AUDIO_SAMPLE_RATE = 24000
_audio_pyaudio = pyaudio.PyAudio()
_audio_stream = None

def _audio_callback(audio_bytes: bytes):
    """TTSRealtimeClient 音频回调: 实时播放并缓存"""
    global _audio_stream
    if _audio_stream is not None:
        try:
            _audio_stream.write(audio_bytes)
        except Exception as exc:
            logging.error(f"PyAudio playback error: {exc}")
    _audio_chunks.append(audio_bytes)
    logging.info(f"Received audio chunk: {len(audio_bytes)} bytes")

def _save_audio_to_file(filename: str = "output.wav", sample_rate: int = 24000) -> bool:
    """将收集到的音频数据保存为 WAV 文件"""
    if not _audio_chunks:
        logging.warning("No audio data to save")
        return False

    try:
        audio_data = b"".join(_audio_chunks)
        with wave.open(filename, 'wb') as wav_file:
            wav_file.setnchannels(1)  # 单声道
            wav_file.setsampwidth(2)  # 16-bit
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(audio_data)
        logging.info(f"Audio saved to: {filename}")
        return True
    except Exception as exc:
        logging.error(f"Failed to save audio: {exc}")
        return False

async def _user_input_loop(client: TTSRealtimeClient):
    """持续获取用户输入并发送文本，当用户输入空文本时发送commit事件并结束本次会话"""
    print("请输入文本（直接按Enter发送commit事件并结束本次会话，按Ctrl+C或Ctrl+D结束整个程序）：")
    
    while True:
        try:
            user_text = input("> ")
            if not user_text:  # 用户输入为空
                # 空输入视为一次对话的结束: 提交缓冲区 -> 结束会话 -> 跳出循环
                logging.info("空输入，发送 commit 事件并结束本次会话")
                await client.commit_text_buffer()
                # 适当等待服务器处理 commit，防止过早结束会话导致丢失音频
                await asyncio.sleep(0.3)
                await client.finish_session()
                break  # 直接退出用户输入循环，无需再次回车
            else:
                logging.info(f"发送文本: {user_text}")
                await client.append_text(user_text)
                
        except EOFError:  # 用户按下Ctrl+D
            break
        except KeyboardInterrupt:  # 用户按下Ctrl+C
            break
    
    # 结束会话
    logging.info("结束会话...")
async def _run_demo():
    """运行完整 Demo"""
    global _audio_stream
    # 打开 PyAudio 输出流
    _audio_stream = _audio_pyaudio.open(
        format=pyaudio.paInt16,
        channels=1,
        rate=_AUDIO_SAMPLE_RATE,
        output=True,
        frames_per_buffer=1024
    )

    client = TTSRealtimeClient(
        base_url=URL,
        api_key=API_KEY,
        voice=VOICE,            # 使用环境变量配置的音色
        mode=SessionMode.COMMIT,   # 使用 COMMIT 模式
        audio_callback=_audio_callback,
        instructions=INSTRUCTIONS  # 传入指令控制内容
    )

    # 建立连接
    await client.connect()

    # 并行执行消息处理与用户输入
    consumer_task = asyncio.create_task(client.handle_messages())
    producer_task = asyncio.create_task(_user_input_loop(client))

    await producer_task  # 等待用户输入完成

    # 等待 response.done
    await client.wait_for_response_done()

    # 关闭连接并取消消费者任务
    await client.close()
    consumer_task.cancel()

    # 关闭音频流
    if _audio_stream is not None:
        _audio_stream.stop_stream()
        _audio_stream.close()
    _audio_pyaudio.terminate()

    # 保存音频数据
    os.makedirs("outputs", exist_ok=True)
    _save_audio_to_file(os.path.join("outputs", "qwen_tts_output.wav"))

def main():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    logging.info("Starting QwenTTS Realtime Client demo…")
    asyncio.run(_run_demo())

if __name__ == "__main__":
    main() 