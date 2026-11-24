import asyncio
import math
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict

import edge_tts
from PIL import Image, ImageDraw, ImageFont
from pydub import AudioSegment

# ==========================================
# 1. 사용자 설정 영역
# ==========================================
START_TIME = "10:00:00"
END_TIME = "10:05:00"
HOLD_AFTER_END_SEC = 30  # 종료 후 대기 화면 유지 시간(초)

WIDTH, HEIGHT = 1280, 720
FPS = 30
OUTPUT_FILE = "clock_alarm_video.mp4"

TTS_VOICE = "ko-KR-SunHiNeural"
TTS_RATE = "+5%"

# 알림 설정: { "HH:MM:SS": "읽어줄 안내 멘트" }
ALARMS: Dict[str, str] = {
    "10:00:10": "시작 10초 경과 안내입니다.",
    "10:03:00": "종료 2분 전입니다.",
    "10:05:00": "시간이 종료되었습니다. 답안 작성을 멈춰주세요.",
}


# ==========================================
# 2. TTS 음성 생성 및 오디오 트랙 합성
# ==========================================
async def _synthesize_tts_async(text: str, output_mp3: Path, voice: str, rate: str) -> None:
    communicate = edge_tts.Communicate(text=text, voice=voice, rate=rate)
    await communicate.save(str(output_mp3))


def synthesize_tts(text: str, output_mp3: Path, voice: str, rate: str) -> None:
    if sys.platform.startswith("win"):
        try:
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        except AttributeError:
            pass
    asyncio.run(_synthesize_tts_async(text, output_mp3, voice, rate))

    if not output_mp3.exists() or output_mp3.stat().st_size == 0:
        raise RuntimeError(f"TTS 파일이 비어 있거나 생성되지 않았습니다: {output_mp3}")


def build_audio_track(
    start_dt: datetime,
    total_duration_sec: float,
    alarms: Dict[str, str],
    temp_dir: Path,
    voice: str,
    rate: str,
) -> Path:
    merged_wav = temp_dir / "merged_audio.wav"
    total_ms = max(1000, int(total_duration_sec * 1000))
    base_audio = AudioSegment.silent(duration=total_ms, frame_rate=44100).set_channels(2)

    placed_count = 0
    for idx, (alarm_str, message) in enumerate(alarms.items()):
        alarm_dt = datetime.strptime(alarm_str.strip(), "%H:%M:%S")
        if alarm_dt < start_dt:
            alarm_dt += timedelta(days=1)

        offset_ms = int((alarm_dt - start_dt).total_seconds() * 1000)
        if offset_ms < 0 or offset_ms > total_ms:
            print(
                f"[경고] 알림 '{alarm_str}'({message})의 오프셋({offset_ms}ms)이 "
                f"전체 영상 길이({total_ms}ms) 범위를 벗어나 건너뜁니다."
            )
            continue

        tts_mp3 = temp_dir / f"alarm_{idx}.mp3"
        print(f"[TTS 생성] {alarm_str} (+{offset_ms / 1000:.1f}초): \"{message}\"")
        synthesize_tts(message, tts_mp3, voice=voice, rate=rate)

        voice_seg = (
            AudioSegment.from_file(str(tts_mp3))
            .set_frame_rate(44100)
            .set_channels(2)
        )
        base_audio = base_audio.overlay(voice_seg, position=offset_ms)
        placed_count += 1

    print(f"[오디오 합성 완료] 배치된 알림 수: {placed_count}개, 총 길이: {len(base_audio) / 1000:.1f}초")
    base_audio.export(str(merged_wav), format="wav")
    return merged_wav


# ==========================================
# 3. 폰트 및 시계 배경·프레임 렌더링
# ==========================================
def load_font(size: int, mono: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    if mono:
        candidates = [
            "C:/Windows/Fonts/consolab.ttf",
            "C:/Windows/Fonts/consola.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
            "/System/Library/Fonts/Menlo.ttc",
        ]
    else:
        candidates = [
            "C:/Windows/Fonts/malgunbd.ttf",
            "C:/Windows/Fonts/malgun.ttf",
            "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf",
            "/System/Library/Fonts/AppleSDGothicNeo.ttc",
        ]

    for path in candidates:
        if os.path.exists(path):
            return ImageFont.truetype(path, size=size)
    return ImageFont.load_default()


def format_hh_mm_ss_ms(delta: timedelta) -> str:
    total_ms = max(0, int(delta.total_seconds() * 1000))
    ms = total_ms % 1000
    total_sec = total_ms // 1000
    hours = total_sec // 3600
    minutes = (total_sec % 3600) // 60
    seconds = total_sec % 60
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}:{ms:03d}"


def create_clock_background(width: int, height: int, cx: int, cy: int, radius: int) -> Image.Image:
    bg = Image.new("RGB", (width, height), (248, 250, 252))
    draw = ImageDraw.Draw(bg)

    draw.ellipse(
        (cx - radius, cy - radius, cx + radius, cy + radius),
        fill=(255, 255, 255),
        outline=(30, 41, 59),
        width=6,
    )

    num_font = load_font(30, mono=False)
    for i in range(60):
        angle = math.radians(i * 6 - 90)
        is_hour = i % 5 == 0
        inner = radius - (22 if is_hour else 10)
        outer = radius - 4

        x1, y1 = cx + inner * math.cos(angle), cy + inner * math.sin(angle)
        x2, y2 = cx + outer * math.cos(angle), cy + outer * math.sin(angle)
        draw.line(
            (x1, y1, x2, y2),
            fill=(15, 23, 42) if is_hour else (148, 163, 184),
            width=4 if is_hour else 2,
        )

        if is_hour:
            hour_num = 12 if i == 0 else i // 5
            label_r = radius - 48
            lx = cx + label_r * math.cos(angle)
            ly = cy + label_r * math.sin(angle)
            label = str(hour_num)
            bbox = draw.textbbox((0, 0), label, font=num_font)
            tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
            draw.text((lx - tw / 2, ly - th / 2), label, fill=(30, 41, 59), font=num_font)

    return bg


def render_clock_frame(
    bg_template: Image.Image,
    current_dt: datetime,
    end_dt: datetime,
    cx: int,
    cy: int,
    radius: int,
    digital_font: ImageFont.ImageFont,
    sub_font: ImageFont.ImageFont,
) -> bytes:
    frame_img = bg_template.copy()
    draw = ImageDraw.Draw(frame_img)

    is_finished = current_dt >= end_dt
    display_dt = end_dt if is_finished else current_dt

    sec = display_dt.second + display_dt.microsecond / 1_000_000
    minute = display_dt.minute + sec / 60
    hour = (display_dt.hour % 12) + minute / 60

    h_angle = math.radians(hour * 30 - 90)
    draw.line(
        (cx, cy, cx + radius * 0.48 * math.cos(h_angle), cy + radius * 0.48 * math.sin(h_angle)),
        fill=(15, 23, 42),
        width=8,
    )

    m_angle = math.radians(minute * 6 - 90)
    draw.line(
        (cx, cy, cx + radius * 0.72 * math.cos(m_angle), cy + radius * 0.72 * math.sin(m_angle)),
        fill=(30, 41, 59),
        width=5,
    )

    s_angle = math.radians(sec * 6 - 90)
    draw.line(
        (cx, cy, cx + radius * 0.84 * math.cos(s_angle), cy + radius * 0.84 * math.sin(s_angle)),
        fill=(220, 38, 38),
        width=2,
    )

    draw.ellipse((cx - 8, cy - 8, cx + 8, cy + 8), fill=(220, 38, 38))

    # 디지털 현재 시각
    time_str = display_dt.strftime("%H:%M:%S")
    bbox = draw.textbbox((0, 0), time_str, font=digital_font)
    tw = bbox[2] - bbox[0]
    draw.text((cx - tw / 2, cy + radius + 20), time_str, fill=(15, 23, 42), font=digital_font)

    # 남은 시간 (HH:MM:SS:mmm) 및 상태 안내
    rem_str = "00:00:00:000" if is_finished else format_hh_mm_ss_ms(end_dt - current_dt)
    if is_finished:
        status_text = f"남은 시간: {rem_str} · 종료되었습니다"
        status_color = (220, 38, 38)
    else:
        status_text = f"남은 시간: {rem_str}"
        status_color = (71, 85, 105)

    sbbox = draw.textbbox((0, 0), status_text, font=sub_font)
    stw = sbbox[2] - sbbox[0]
    draw.text((cx - stw / 2, cy + radius + 68), status_text, fill=status_color, font=sub_font)

    return frame_img.tobytes()


def _drain_stderr(pipe, log_lines: list[str]) -> None:
    try:
        for line in iter(pipe.readline, b""):
            decoded = line.decode("utf-8", errors="replace").strip()
            if decoded:
                log_lines.append(decoded)
    finally:
        pipe.close()


# ==========================================
# 4. 메인 실행 파이프라인
# ==========================================
def main() -> None:
    ffmpeg_path = shutil.which("ffmpeg")
    ffprobe_path = shutil.which("ffprobe")
    if ffmpeg_path is None:
        raise RuntimeError("시스템에서 ffmpeg를 찾을 수 없습니다. PATH 설정을 확인해 주세요.")
    if ffprobe_path is None:
        raise RuntimeError("시스템에서 ffprobe를 찾을 수 없습니다. ffmpeg 설치 폴더의 ffprobe.exe PATH를 확인해 주세요.")

    AudioSegment.converter = ffmpeg_path
    AudioSegment.ffprobe = ffprobe_path

    start_dt = datetime.strptime(START_TIME, "%H:%M:%S")
    end_dt = datetime.strptime(END_TIME, "%H:%M:%S")
    if end_dt <= start_dt:
        end_dt += timedelta(days=1)

    active_sec = (end_dt - start_dt).total_seconds()
    total_sec = active_sec + max(0.0, HOLD_AFTER_END_SEC)
    total_frames = max(1, int(total_sec * FPS))

    temp_dir = Path(tempfile.mkdtemp(prefix="clock_vid_"))
    try:
        audio_path = build_audio_track(
            start_dt=start_dt,
            total_duration_sec=total_sec,
            alarms=ALARMS,
            temp_dir=temp_dir,
            voice=TTS_VOICE,
            rate=TTS_RATE,
        )

        cx, cy = WIDTH // 2, HEIGHT // 2 - 45
        radius = 215
        bg_template = create_clock_background(WIDTH, HEIGHT, cx, cy, radius)
        digital_font = load_font(36, mono=True)
        sub_font = load_font(22, mono=True)

        ffmpeg_cmd = [
            ffmpeg_path,
            "-y",
            "-loglevel", "error",
            "-f", "rawvideo",
            "-vcodec", "rawvideo",
            "-s", f"{WIDTH}x{HEIGHT}",
            "-pix_fmt", "rgb24",
            "-r", str(FPS),
            "-i", "-",
            "-i", str(audio_path),
            "-map", "0:v:0",
            "-map", "1:a:0",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-b:a", "192k",
            "-shortest",
            OUTPUT_FILE,
        ]

        print(f"영상 렌더링 시작 (총 {total_frames} 프레임, 약 {total_sec:.1f}초)...")
        render_start = time.perf_counter()
        stderr_lines: list[str] = []

        process = subprocess.Popen(
            ffmpeg_cmd,
            stdin=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert process.stdin is not None
        assert process.stderr is not None

        stderr_thread = threading.Thread(
            target=_drain_stderr,
            args=(process.stderr, stderr_lines),
            daemon=True,
        )
        stderr_thread.start()

        log_interval = max(1, FPS)  # 약 1초마다 진행률 출력
        try:
            for frame_idx in range(total_frames):
                current_dt = start_dt + timedelta(seconds=frame_idx / FPS)
                raw_bytes = render_clock_frame(
                    bg_template=bg_template,
                    current_dt=current_dt,
                    end_dt=end_dt,
                    cx=cx,
                    cy=cy,
                    radius=radius,
                    digital_font=digital_font,
                    sub_font=sub_font,
                )
                process.stdin.write(raw_bytes)

                if (frame_idx + 1) % log_interval == 0 or (frame_idx + 1) == total_frames:
                    pct = ((frame_idx + 1) / total_frames) * 100
                    elapsed = time.perf_counter() - render_start
                    print(
                        f"\r[렌더링] {frame_idx + 1}/{total_frames} ({pct:5.1f}%) | 경과: {elapsed:.1f}s",
                        end="",
                        flush=True,
                    )
        finally:
            print()
            process.stdin.close()

        ret = process.wait()
        stderr_thread.join(timeout=2.0)

        if ret != 0:
            err_msg = "\n".join(stderr_lines)
            raise RuntimeError(f"FFmpeg 인코딩 실패 (exit code: {ret})\n{err_msg}")

        print(f"완료! 저장된 파일: {Path(OUTPUT_FILE).resolve()}")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    main()