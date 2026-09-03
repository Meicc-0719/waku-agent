"""Voice gateway — talk to your laptop, it talks back.

    pip install -e '.[voice]'
    make voice

Push-to-talk MVP: press Enter, speak, press Enter again. Your speech runs
through the exact same loop/memory/eval pipeline as typed text — a gateway
only moves words in and out (that's the whole point of the gateway box).

  ears   faster-whisper (local Whisper, ~74MB model downloads on first run)
  voice  macOS `say` with a British voice by default (zero setup), or the
         neural Kokoro voice if installed:  pip install kokoro soundfile
         then set WAKU_TTS=kokoro  (WAKU_VOICE=bm_george / bm_fable / ...)

Wake-word mode ("hey <name>, ...") is deliberately v2 — see docs/roadmap:
openWakeWord can train a custom wake word for whatever we name this thing.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys

from waku.app import Waku
from waku.gateway.cli import _observer  # 也在语音模式下显示门/工具线

SAMPLE_RATE = 16000


def record_until_enter():
    """Capture mic audio between two Enter presses; returns a float32 array."""
    import numpy as np
    import sounddevice as sd

    frames: list[np.ndarray] = []

    def collect(indata, frame_count, time_info, status):
        frames.append(indata.copy())

    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32", callback=collect):
        input("recording — press Enter when done… ")
    if not frames:
        return np.zeros(0, dtype="float32")
    return np.concatenate(frames)[:, 0]


class Ears:
    def __init__(self, model_size: str | None = None):
        from faster_whisper import WhisperModel

        self.model = WhisperModel(
            model_size or os.getenv("WAKU_WHISPER_MODEL", "base"),
            compute_type="int8",
        )

    def transcribe(self, audio, language: str | None = None) -> str:
        segments, _ = self.model.transcribe(
            audio, language=language or os.getenv("WAKU_WHISPER_LANG")
        )
        return " ".join(seg.text.strip() for seg in segments).strip()


def _best_say_voice() -> str:
    """Pick the nicest available macOS voice: prefer a downloaded Premium/Enhanced
    (near-Siri quality) voice, then a decent compact one. Beats hardcoding a
    robotic default — and auto-upgrades the moment you download a better voice in
    System Settings ▸ Accessibility ▸ Spoken Content ▸ System Voice."""
    try:
        out = subprocess.run(["say", "-v", "?"], capture_output=True, text=True, timeout=5, check=False).stdout
    except Exception:
        return "Daniel"
    # 每行：“Name (variant) en_US # example” — 从语言环境列中分割名称
    english = []
    for ln in out.splitlines():
        m = re.match(r"^(.+?)\s{2,}([a-z]{2})_", ln)
        if m and m.group(2) == "en":
            english.append(m.group(1).strip())
    # 第一个：下载的高质量语音（高级>增强）
    for tag in ("(Premium)", "(Enhanced)"):
        hit = next((n for n in english if tag in n), None)
        if hit:
            return hit
    # 否则是合理紧凑的声音（如果存在）
    for pref in ("Serena", "Kate", "Daniel", "Samantha"):
        if pref in english:
            return pref
    return english[0] if english else "Daniel"


# 表情符号和象形文字 — TTS 引擎会大声读出这些内容（“火箭”、“闪闪发光”），
# 这听起来很荒谬。在说话之前剥掉它们（加上剩余的降价项目符号）。
_EMOJI = re.compile(
    "[\U0001f300-\U0001faff"  # 符号、象形文字、表情符号、运输、补充
    "\U00002600-\U000027bf"    # 杂项符号 + 装饰符号
    "\U0001f1e6-\U0001f1ff"    # 区域指标标志字母
    "\U00002190-\U000021ff"    # 箭头
    "\U00002b00-\U00002bff"    # 星星、其他符号和箭头
    "\U0000fe00-\U0000fe0f"    # 变体选择器
    "\U0000200d\U000020e3\U0000fe0f]+"  # ZWJ + 键帽/变体连接器
)


def _speakable(text: str) -> str:
    """What actually gets voiced: no emoji, no stray markdown markers, tidy spaces."""
    if not text:
        return ""
    text = _EMOJI.sub("", text)
    text = re.sub(r"[*_`#>]", "", text)      # Markdown 强调/标题/引号/代码标记
    text = re.sub(r"[ \t]{2,}", " ", text)   # 折叠已删除字形留下的间隙
    return "\n".join(ln.strip() for ln in text.splitlines()).strip()


class Mouth:
    """TTS with a boring, reliable default (macOS `say`) and a neural upgrade
    (Kokoro-82M, Apache-2.0 — its bm_* voices are the proper British butler)."""

    def __init__(self):
        self.engine = os.getenv("WAKU_TTS", "").strip().lower()
        self.voice = os.getenv("WAKU_VOICE", "")
        if not self.engine:
            # 自动：如果已安装，请使用更好的神经语音 (Kokoro)，否则
            # 回退到 macOS `say`。所以单独`pip install kokoro soundfile`
            # 升级语音——无需环境变量。
            try:
                import kokoro  # noqa: F401

                self.engine = "kokoro"
            except ImportError:
                self.engine = "say"
        if self.engine == "kokoro":
            from kokoro import KPipeline

            self.pipeline = KPipeline(lang_code="b")  # b = 英式英语
            self.voice = self.voice or "bm_george"
        elif self.engine == "say" and not self.voice:
            self.voice = _best_say_voice()  # 自动升级到高级/增强语音

    def speak(self, text: str) -> None:
        text = _speakable(text)
        if not text:
            return
        if self.engine == "kokoro":
            import sounddevice as sd

            for _, _, audio in self.pipeline(text, voice=self.voice):
                sd.play(audio, 24000)
                sd.wait()
        elif sys.platform == "darwin":
            subprocess.run(["say", "-v", self.voice or "Daniel", text], check=False)
        else:
            print("(no TTS engine on this platform — set WAKU_TTS=kokoro)")


def matches_wake(text: str, wake_word: str) -> bool:
    """Does a transcript contain the (customizable) wake word?

    Fuzzy on purpose: Whisper hears "waku waku" as "wakuwaku", "Waku, waku!",
    "walku waku" — or transcribes it as Japanese kana わくわく (the first live
    test!). So: `wake_word` accepts comma-separated variants across scripts
    ("waku waku,わくわく"), normalization keeps kana AND CJK, and matching is
    substring + sliding-window similarity. Pure function → deterministic evals.
    """
    import difflib
    import re

    def norm(s: str) -> str:
        # keep latin, digits, hiragana/katakana (぀-ヿ), CJK ideographs (一-鿿)
        return re.sub(r"[^a-z0-9぀-ヿ一-鿿 ]", "", s.lower()).strip()

    heard = norm(text)
    if not heard:
        return False

    for variant in (v for v in (norm(v) for v in wake_word.split(",")) if v):
        if variant in heard or variant.replace(" ", "") in heard.replace(" ", ""):
            return True
        words, n = heard.split(), len(variant.split())
        if any(
            difflib.SequenceMatcher(None, " ".join(words[i : i + n]), variant).ratio() >= 0.7
            for i in range(max(0, len(words) - n + 1))
        ):
            return True
    return False


def _mic_threshold() -> float:
    """RMS below this = silence. Mics vary wildly — tune with
    WAKU_MIC_THRESHOLD (lower if it never hears you, higher if it
    wakes on room noise)."""
    return float(os.getenv("WAKU_MIC_THRESHOLD", "0.005"))


def record_command(stream, max_seconds: float = 15.0, silence_after: float = 1.2):
    """After the wake word: keep reading the SAME stream until the speaker
    goes quiet. Reusing the stream matters — opening a fresh macOS audio
    stream per phase is how the first version froze."""
    import numpy as np

    block = SAMPLE_RATE // 10  # 100ms 块
    frames, quiet, spoke = [], 0, False
    for _ in range(int(max_seconds * 10)):
        data, _ = stream.read(block)
        frames.append(data.copy())
        loud = float(np.sqrt((data**2).mean())) > _mic_threshold() * 2
        spoke = spoke or loud
        quiet = 0 if loud else quiet + 1
        if spoke and quiet >= int(silence_after * 10):
            break
    return np.concatenate(frames)[:, 0]


def wait_for_speech(stream, timeout: float) -> bool:
    """Poll the SAME stream for the onset of speech, up to `timeout` seconds.
    Returns True the moment the mic goes loud, False if it stays quiet — lets a
    conversation stay open for follow-ups (Siri-style) without the wake word."""
    import numpy as np

    block = SAMPLE_RATE // 10
    for _ in range(int(timeout * 10)):
        data, _ = stream.read(block)
        if float(np.sqrt((data**2).mean())) > _mic_threshold() * 2:
            return True
    return False


def wake_loop(waku: Waku, mouth: Mouth, wake_word: str) -> None:
    """Always-listening mode: scan the mic in ~2.5s windows with the tiny
    Whisper model until the wake word shows up, then hand off to the big one.

    This is the transparent, zero-training way to make ANY phrase a wake word.
    Trade-off vs a real wake-word engine (openWakeWord): a bit more CPU and a
    chunk boundary can occasionally split the phrase — say it with intent.

    Engineering notes from the first live freeze:
    - ONE persistent InputStream for everything. sd.rec()+sd.wait() per chunk
      re-opens the device every 2.5s and can block forever when macOS audio
      routing changes (say/AirPods/etc).
    - The scanner always shows a heartbeat, so "listening" never looks "dead".
    - The mic buffer is drained after Waku speaks, so it doesn't wake on
      the tail of its own voice (the "mm-hmm" self-trigger in the trace).
    """
    import numpy as np
    import sounddevice as sd

    scout = Ears(model_size="tiny")  # 便宜，永远在线
    ears = Ears()                    # 准确，仅在唤醒后
    ack = os.getenv("WAKU_WAKE_ACK", "Yes?")
    followup = float(os.getenv("WAKU_FOLLOWUP_SECONDS", "8"))  # 保持开放，Siri 风格
    block = SAMPLE_RATE // 10
    # 固定侦察员的转录语言以匹配唤醒词的脚本 -
    # 否则 Whisper 会听到“waku waku”并帮忙写出わくわく，这
    # 拉丁语唤醒词永远不会匹配。唤醒后的命令仍会自动检测。
    wake_lang = os.getenv("WAKU_WAKE_LANG") or ("en" if wake_word.isascii() else None)
    print(f'Listening for "{wake_word}" — Ctrl-C to quit.')

    def status(msg: str) -> None:
        sys.stdout.write(f"\r\x1b[2m{msg[:72]:<72}\x1b[0m")
        sys.stdout.flush()

    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32", blocksize=block) as stream:

        def drain() -> None:
            while stream.read_available >= block:
                stream.read(block)

        window: list = []
        while True:
            data, _ = stream.read(block)
            window.append(data.copy())
            if len(window) < 25:  # 聚集2.5秒
                continue
            chunk = np.concatenate(window)[:, 0]
            window = window[-5:]  # 保留 0.5 秒的尾部，以便该短语可以跨越多个块

            if float(np.sqrt((chunk**2).mean())) < _mic_threshold():
                status("· listening…")
                continue
            heard_scan = scout.transcribe(chunk, language=wake_lang)
            if not matches_wake(heard_scan, wake_word):
                status(f'· heard: "{heard_scan}"' if heard_scan else "· listening…")
                if heard_scan:  # 未遂事件属于跟踪（唤醒调整！）
                    waku.tracer.event("wake_scan", {"heard": heard_scan, "matched": False})
                continue

            print("\n[wake word]")
            mouth.speak(ack)
            drain()  # 不要转录通过麦克风播放的 ack

            # 醒来后继续谈话：回答，然后继续听
            # 对于“followup”秒的后续行动 - 无需说“waku waku”
            # 再次（如 Siri）。安静的一段时间后会回到唤醒词模式。
            while True:
                heard = ears.transcribe(record_command(stream))
                if heard:
                    print(f"you › {heard}")
                    result = waku.respond(heard, observer=_observer, source="voice")
                    print(f"waku › {result.reply}")
                    mouth.speak(result.reply)
                else:
                    print("(didn't catch that)")
                drain()  # ...并且不要在回复的尾部醒来
                status(f"· still here — just talk, or I'll rest in {followup:.0f}s")
                if not wait_for_speech(stream, followup):
                    break  # 安静 → 返回聆听唤醒词
            window = []


def main() -> None:
    try:
        import sounddevice  # noqa: F401
    except ImportError:
        raise SystemExit("Voice extra not installed: pip install -e '.[voice]'")

    waku = Waku()
    waku.session.session_id = "voice"   # 收件箱中有自己的对话线程
    mouth = Mouth()

    # 默认免提：始终收听“waku waku”。默认包
    # 微型扫描仪会误听它的方式（wakuwaku / waka waka / kana），所以
    # 它可靠地触发。将 WAKU_WAKE_WORD="" 设置为一键通。
    wake_word = os.getenv(
        "WAKU_WAKE_WORD", "waku waku,wakuwaku,waku,waka waka,wako wako,walk walk,わくわく"
    ).strip()
    if wake_word:
        try:
            wake_loop(waku, mouth, wake_word)
        except KeyboardInterrupt:
            pass
        print("\nbye — your memory stays in state.db")
        return

    ears = Ears()
    print("Voice Waku ready. Press Enter to talk, Ctrl-C to quit.")
    while True:
        try:
            input("\npress Enter to talk… ")
            audio = record_until_enter()
        except (EOFError, KeyboardInterrupt):
            break
        if audio.size < SAMPLE_RATE // 4:  # 低于 250 毫秒——可能是一个失误
            print("(too short, try again)")
            continue

        heard = ears.transcribe(audio)
        if not heard:
            print("(didn't catch that)")
            continue
        print(f"you › {heard}")

        result = waku.respond(heard, observer=_observer, source="voice")
        print(f"waku › {result.reply}")
        mouth.speak(result.reply)

    print("bye — your memory stays in state.db")


if __name__ == "__main__":
    main()
