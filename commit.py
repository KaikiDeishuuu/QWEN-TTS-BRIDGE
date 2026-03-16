import os
import asyncio
import logging
import wave
from datetime import datetime
from tts_realtime_client import TTSRealtimeClient, SessionMode
# import pyaudio  # 移除 PyAudio 依赖

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
# _audio_pyaudio = pyaudio.PyAudio()  # 移除
_audio_stream = None # 用于实时播放的流 (已禁用)
_stream_file = None # [NEW] 用于流式写入的原始音频文件

def _audio_callback(audio_bytes: bytes):
    """TTSRealtimeClient 音频回调: 写入文件并缓存"""
    global _stream_file
    
    # 实时写入原始音频文件 (PCM)
    if _stream_file:
        _stream_file.write(audio_bytes)
        _stream_file.flush()
        logging.info(f"Received and streamed audio chunk: {len(audio_bytes)} bytes")
    
    _audio_chunks.append(audio_bytes)


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
    """持续获取用户输入并发送文本。输入空行触发一次合成，输入 'exit' 或 'quit' 结束程序。"""
    global _audio_chunks, _stream_file
    
    print("\n" + "="*50)
    print("已进入持久会话模式。您可以连续输入多段文本。")
    print("操作指引:")
    print("1. 输入文本并按回车: 将文本存入缓冲区")
    print("2. 直接按回车 (空行): 触发合成并保存当前所有文本的语音")
    print("3. 输入 'exit' 或 'quit': 结束本次测试并关闭连接")
    print("="*50 + "\n")
    
    while True:
        try:
            user_text = input("> ").strip()
            
            if user_text.lower() in ["exit", "quit"]:
                logging.info("收到退出指令，准备关闭会话...")
                break
                
            if not user_text:
                # 检查是否有内容需要 Commit
                print("\n--- 检测到空输入，触发 Commit 语音合成... ---")
                
                # 1. 准备文件名 (PCM 和 WAV)
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                os.makedirs("outputs", exist_ok=True)
                pcm_path = os.path.join("outputs", f"output_{timestamp}.pcm")
                wav_path = os.path.join("outputs", f"qwen_tts_{timestamp}.wav")
                
                # 2. 开启原始音频流写入
                _stream_file = open(pcm_path, "wb")
                
                # 3. 发送 Commit
                await client.commit_text_buffer()
                
                # 4. 等待服务器合成结束
                print("服务器正在合成中，请稍候...")
                await client.wait_for_response_done()
                
                # 5. 等待一小会儿确保所有音频 chunk 已经过回调处理
                await asyncio.sleep(0.5)
                
                # 6. 清理与保存
                if _stream_file:
                    _stream_file.close()
                    _stream_file = None
                    
                _save_audio_to_file(wav_path)
                
                # [CRITICAL] 清空音频缓存，为下一次合成做准备
                _audio_chunks = []
                
                print(f"--- 合成任务完成！ ---")
                print(f"PCM: {pcm_path}\nWAV: {wav_path}\n")
                print("您可以继续输入下一段文字：")
                continue
            else:
                logging.info(f"发送文本: {user_text}")
                await client.append_text(user_text)
                print("(文本已加入缓冲区，按 [回车键] 或输入更多文字)")
                
        except EOFError:
            break
        except KeyboardInterrupt:
            break
async def _run_demo():
    """运行完整 Demo"""
    global _stream_file
    
    client = TTSRealtimeClient(
        base_url=URL,
        api_key=API_KEY,
        voice=VOICE,
        mode=SessionMode.COMMIT,
        audio_callback=_audio_callback,
        instructions=INSTRUCTIONS
    )

    # 建立持久 WebSocket 连接
    await client.connect()
    logging.info("WebSocket 连接已建立，会话已开启。")

    # 并行执行消息处理
    consumer_task = asyncio.create_task(client.handle_messages())
    
    # 执行交互式循环 (长连接，多次复用)
    try:
        await _user_input_loop(client)
    finally:
        # 清理工作：关闭服务器会话并断开连接
        logging.info("正在清理并关闭连接...")
        await client.finish_session()
        await client.close()
        consumer_task.cancel()
        
        # 确保文件句柄被正常关闭
        if _stream_file:
            _stream_file.close()
            _stream_file = None

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