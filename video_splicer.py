# -*- coding: utf-8 -*-
import argparse
import random
import shutil
import subprocess
import sys
from bisect import bisect_left
from datetime import datetime
from pathlib import Path
from time import perf_counter

# --- 参数配置 ---
BASE_DIR = Path(__file__).resolve().parent
SOURCE_DIR = BASE_DIR / "source_videos"
OUTPUT_DIR = BASE_DIR / "output_videos"
SOURCE_LAYERS_DIR = BASE_DIR / "source_layers"
SOURCE_AUDIOS_DIR = BASE_DIR / "source_audios"

DEFAULT_NUM_OUTPUT_VIDEOS = 25
MIN_SEGMENT_DURATION = 1.0
POSSIBLE_DURATIONS = [17, 18, 19, 20]
DEFAULT_MIN_CLIPS_PER_VIDEO = 6
DEFAULT_MAX_CLIPS_PER_VIDEO = 9
MAX_GENERATION_ATTEMPTS = 200

WIDTH = 1080
HEIGHT = 1920
FPS = 25
UI_WIDTH = 76


def parse_args():
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(description="随机拼接视频并添加水印与背景音乐。")
    parser.add_argument(
        "--count",
        type=int,
        default=None,
        help="要生成的视频数量；不传时会在启动后交互询问。",
    )
    parser.add_argument(
        "--min-clips",
        type=int,
        default=DEFAULT_MIN_CLIPS_PER_VIDEO,
        help=f"每条视频的最少片段数，默认 {DEFAULT_MIN_CLIPS_PER_VIDEO}。",
    )
    parser.add_argument(
        "--max-clips",
        type=int,
        default=DEFAULT_MAX_CLIPS_PER_VIDEO,
        help=f"每条视频的最多片段数，默认 {DEFAULT_MAX_CLIPS_PER_VIDEO}。",
    )
    args = parser.parse_args()

    if args.count is not None and args.count <= 0:
        parser.error("--count 必须大于 0。")
    if args.min_clips <= 0:
        parser.error("--min-clips 必须大于 0。")
    if args.max_clips <= 0:
        parser.error("--max-clips 必须大于 0。")
    if args.min_clips > args.max_clips:
        parser.error("--min-clips 不能大于 --max-clips。")
    if args.min_clips * MIN_SEGMENT_DURATION > max(POSSIBLE_DURATIONS):
        parser.error("当前 --min-clips 与最小时长组合不合法，请减少片段数或降低最小时长。")

    return args


def prompt_for_video_count():
    """在未传入 --count 时，交互式询问本次生成数量。"""
    while True:
        user_input = input(f"这次要生成多少条？直接回车则使用默认 {DEFAULT_NUM_OUTPUT_VIDEOS} 条: ").strip()
        if not user_input:
            return DEFAULT_NUM_OUTPUT_VIDEOS

        try:
            count = int(user_input)
        except ValueError:
            print_result_line("warn", "请输入正整数，例如 10、25、40。")
            continue

        if count <= 0:
            print_result_line("warn", "生成条数必须大于 0。")
            continue

        return count


def check_required_tools():
    """检查 ffmpeg 和 ffprobe 是否可用。"""
    for tool_name in ("ffmpeg", "ffprobe"):
        if shutil.which(tool_name) is None:
            raise EnvironmentError(
                f"错误：未找到 '{tool_name}'，请先安装 FFmpeg 并确保命令可在终端中直接运行。"
            )


def inject_ffmpeg_progress_args(command):
    """为 ffmpeg 命令注入更安静且可解析的进度输出参数。"""
    if not command or command[0] != "ffmpeg":
        return command

    return [
        command[0],
        "-hide_banner",
        "-loglevel",
        "error",
        "-progress",
        "pipe:1",
        "-nostats",
        *command[1:],
    ]


def format_duration(seconds):
    """将秒数格式化为 mm:ss。"""
    total_seconds = max(0, int(round(seconds)))
    minutes, secs = divmod(total_seconds, 60)
    return f"{minutes:02d}:{secs:02d}"


def format_elapsed_time(seconds):
    """将耗时格式化为 hh:mm:ss。"""
    total_seconds = max(0, int(round(seconds)))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def truncate_text(value, max_length):
    """截断过长文本，避免终端布局被撑破。"""
    text = str(value)
    if len(text) <= max_length:
        return text
    if max_length <= 1:
        return text[:max_length]
    return text[: max_length - 1] + "…"


def build_progress_bar(percent, width=28):
    """构建简洁的 ASCII 进度条。"""
    safe_percent = max(0, min(100, percent))
    filled = int(width * safe_percent / 100)
    return f"[{'#' * filled}{'.' * (width - filled)}]"


def render_progress_line(label, percent, current_seconds, total_seconds):
    """渲染一行单条任务进度。"""
    current_text = format_duration(current_seconds)
    total_text = format_duration(total_seconds)
    progress_bar = build_progress_bar(percent)
    safe_label = truncate_text(label, 12)
    return f"  🎬 {safe_label:<12} {progress_bar} {percent:>3d}%  {current_text} / {total_text}"


def print_progress_line(label, percent, current_seconds, total_seconds):
    """以同一行动态刷新的方式打印进度信息。"""
    line = render_progress_line(label, percent, current_seconds, total_seconds)
    sys.stdout.write("\r" + line.ljust(96))
    sys.stdout.flush()


def finish_progress_line():
    """结束动态进度行，换到下一行。"""
    sys.stdout.write("\n")
    sys.stdout.flush()


def print_divider(char="─"):
    """打印一条统一宽度的分隔线。"""
    print(char * UI_WIDTH)


def print_banner(title, subtitle=None):
    """打印启动横幅。"""
    print()
    print("╔" + "═" * (UI_WIDTH - 2) + "╗")
    print(f"║{title.center(UI_WIDTH - 2)}║")
    if subtitle:
        print(f"║{subtitle.center(UI_WIDTH - 2)}║")
    print("╚" + "═" * (UI_WIDTH - 2) + "╝")


def print_section_title(title):
    """打印更清晰的终端分节标题。"""
    text = f" {title} "
    side_width = max(0, (UI_WIDTH - len(text)) // 2)
    line = f"\n{'─' * side_width}{text}{'─' * (UI_WIDTH - len(text) - side_width)}"
    print(line)


def print_status(label, value):
    """打印统一风格的状态行。"""
    safe_value = truncate_text(value, UI_WIDTH - 18)
    print(f"  {label:<12} {safe_value}")


def print_kv_grid(items):
    """打印更整齐的状态块。"""
    for label, value in items:
        print_status(f"{label}:", value)


def print_result_line(status, value):
    """打印带状态前缀的结果行。"""
    prefix_map = {
        "success": "✅",
        "warn": "⚠️ ",
        "error": "❌",
        "info": "✨",
        "retry": "🔁",
        "stage": "🎯",
        "summary": "🏁",
    }
    prefix = prefix_map.get(status, "•")
    print(f"{prefix} {value}")


def print_panel(title, rows, icon="◼"):
    """打印卡片式信息面板。"""
    inner_width = UI_WIDTH - 4
    print(f"┌{'─' * (UI_WIDTH - 2)}┐")
    panel_title = f"{icon} {title}"
    print(f"│ {truncate_text(panel_title, inner_width):<{inner_width}} │")
    print(f"├{'─' * (UI_WIDTH - 2)}┤")
    for label, value in rows:
        line = f"{label:<12} {value}"
        print(f"│ {truncate_text(line, inner_width):<{inner_width}} │")
    print(f"└{'─' * (UI_WIDTH - 2)}┘")


def run_ffmpeg_command(command, progress_label=None, expected_duration=None):
    """执行 FFmpeg 命令，失败时输出错误信息，并在可能时展示简洁进度。"""
    effective_command = inject_ffmpeg_progress_args(command)
    progress_started = False

    try:
        if effective_command and effective_command[0] == "ffmpeg":
            process = subprocess.Popen(
                effective_command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )

            last_reported_percent = -1
            if process.stdout is not None:
                for raw_line in process.stdout:
                    line = raw_line.strip()
                    if not line or "=" not in line:
                        continue

                    key, value = line.split("=", 1)
                    if (
                        key == "out_time_ms"
                        and expected_duration
                        and expected_duration > 0
                        and progress_label
                    ):
                        if value == "N/A":
                            continue

                        try:
                            current_seconds = int(value) / 1_000_000
                        except ValueError:
                            continue

                        percent = min(100, int((current_seconds / expected_duration) * 100))

                        if percent != last_reported_percent:
                            print_progress_line(
                                progress_label,
                                percent,
                                current_seconds,
                                expected_duration,
                            )
                            progress_started = True
                            last_reported_percent = percent

            stderr_output = process.stderr.read() if process.stderr is not None else ""
            return_code = process.wait()
            if progress_started:
                finish_progress_line()
                progress_started = False
            if return_code != 0:
                raise subprocess.CalledProcessError(
                    return_code,
                    effective_command,
                    stderr=stderr_output,
                )
        else:
            subprocess.run(effective_command, check=True, text=True)
    except FileNotFoundError as exc:
        if progress_started:
            finish_progress_line()
        raise EnvironmentError(
            f"错误：无法执行 '{command[0]}'，请确认 FFmpeg 已正确安装。"
        ) from exc
    except subprocess.CalledProcessError as exc:
        if progress_started:
            finish_progress_line()
        print("--- FFmpeg 命令执行失败 ---")
        print("命令:", " ".join(map(str, command)))
        print(f"退出码: {exc.returncode}")
        if exc.stderr:
            print("错误输出:")
            print(exc.stderr.strip())
        raise


def get_video_duration(video_path):
    """使用 ffprobe 获取视频时长。"""
    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(video_path),
    ]
    try:
        result = subprocess.run(command, check=True, capture_output=True, text=True)
        return float(result.stdout.strip())
    except FileNotFoundError as exc:
        raise EnvironmentError("错误：未找到 ffprobe，请确认 FFmpeg 已正确安装。") from exc
    except (subprocess.CalledProcessError, ValueError):
        return None


def generate_random_durations(total_duration, num_segments, min_duration):
    """生成一组总和固定的随机时长。"""
    if num_segments * min_duration > total_duration:
        raise ValueError("错误：最小片段时长的总和已超过视频总时长！")

    base_duration_total = num_segments * min_duration
    remaining_duration = total_duration - base_duration_total
    cuts = sorted(random.uniform(0, remaining_duration) for _ in range(num_segments - 1))

    random_parts = []
    last_cut = 0
    for cut in cuts:
        random_parts.append(cut - last_cut)
        last_cut = cut
    random_parts.append(remaining_duration - last_cut)

    final_durations = [min_duration + part for part in random_parts]
    random.shuffle(final_durations)
    return final_durations


def load_media_files(directory, extensions):
    """读取指定目录下的素材文件名。"""
    return sorted(
        file_path.name
        for file_path in directory.iterdir()
        if file_path.is_file()
        and file_path.suffix.lower() in extensions
        and not file_path.name.startswith(".")
    )


def build_video_duration_cache(video_names):
    """启动时预读取所有视频时长，避免重复调用 ffprobe。"""
    duration_cache = {}
    invalid_videos = []

    for video_name in video_names:
        video_path = SOURCE_DIR / video_name
        duration = get_video_duration(video_path)
        if duration is None or duration <= 0:
            invalid_videos.append(video_name)
            continue
        duration_cache[video_name] = duration

    return duration_cache, invalid_videos


def build_sorted_video_pool(duration_cache):
    """构建按时长排序的视频池，便于快速筛选候选素材。"""
    return sorted((duration, video_name) for video_name, duration in duration_cache.items())


def choose_balanced_item(items, usage_counts):
    """少用优先，同使用次数内随机选择。"""
    min_usage = min(usage_counts[item] for item in items)
    candidates = [item for item in items if usage_counts[item] == min_usage]
    selected_item = random.choice(candidates)
    usage_counts[selected_item] += 1
    return selected_item


def select_balanced_overlay_and_audio(layer_usage_counts, audio_usage_counts):
    """均衡选择水印和音频素材。"""
    selected_layer = choose_balanced_item(list(layer_usage_counts.keys()), layer_usage_counts)
    selected_audio = choose_balanced_item(list(audio_usage_counts.keys()), audio_usage_counts)
    return selected_layer, selected_audio


def select_clips_for_video(random_durations, sorted_video_pool, video_usage_counts):
    """根据目标时长进行高效且均衡的视频素材选择。"""
    clips_to_process = []
    used_videos = set()
    sorted_durations = [duration for duration, _ in sorted_video_pool]

    for duration_needed in sorted(random_durations, reverse=True):
        start_idx = bisect_left(sorted_durations, duration_needed)
        candidate_names = [
            video_name
            for candidate_duration, video_name in sorted_video_pool[start_idx:]
            if candidate_duration >= duration_needed and video_name not in used_videos
        ]

        if not candidate_names:
            return None

        min_usage = min(video_usage_counts[name] for name in candidate_names)
        least_used_candidates = [
            name for name in candidate_names if video_usage_counts[name] == min_usage
        ]
        selected_name = random.choice(least_used_candidates)
        selected_duration = next(
            candidate_duration
            for candidate_duration, video_name in sorted_video_pool[start_idx:]
            if video_name == selected_name
        )

        clips_to_process.append(
            {
                "name": selected_name,
                "duration_needed": duration_needed,
                "source_duration": selected_duration,
            }
        )
        used_videos.add(selected_name)

    random.shuffle(clips_to_process)
    for clip_info in clips_to_process:
        video_usage_counts[clip_info["name"]] += 1

    return clips_to_process


def build_usage_counts(items):
    """初始化素材使用次数。"""
    return {item: 0 for item in items}


def rollback_video_usage(clips_to_process, video_usage_counts):
    """在单次尝试失败时回滚视频素材使用次数。"""
    if not clips_to_process:
        return

    for clip_info in clips_to_process:
        video_name = clip_info["name"]
        if video_usage_counts[video_name] > 0:
            video_usage_counts[video_name] -= 1


def print_usage_summary(title, usage_counts, limit=5):
    """打印素材使用次数摘要。"""
    sorted_items = sorted(usage_counts.items(), key=lambda item: (item[1], item[0]))
    least_used = ", ".join(f"{name}({count})" for name, count in sorted_items[:limit])
    most_used = ", ".join(
        f"{name}({count})"
        for name, count in sorted(
            usage_counts.items(),
            key=lambda item: (-item[1], item[0]),
        )[:limit]
    )
    print_status(f"{title}低频:", least_used)
    print_status(f"{title}高频:", most_used)


def create_run_id():
    """为当前运行生成时间戳。"""
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def build_output_path(run_id, video_number):
    """生成直接输出到 output_videos 的文件名。"""
    return OUTPUT_DIR / f"{run_id}_video_{video_number:03d}.mp4"


def build_single_pass_ffmpeg_command(clips_to_process, layer_path, audio_path, final_output_path):
    """构建单次输出成片的 FFmpeg 命令。"""
    command = ["ffmpeg", "-y"]

    for clip_info in clips_to_process:
        input_path = SOURCE_DIR / clip_info["name"]
        source_duration = clip_info["source_duration"]
        segment_duration = clip_info["duration_needed"]
        random_start_time = random.uniform(0, source_duration - segment_duration)
        clip_info["random_start_time"] = random_start_time

        command.extend(
            [
                "-hwaccel",
                "videotoolbox",
                "-ss",
                f"{random_start_time:.6f}",
                "-t",
                f"{segment_duration:.6f}",
                "-i",
                str(input_path),
            ]
        )

    layer_input_index = len(clips_to_process)
    audio_input_index = layer_input_index + 1
    command.extend(["-i", str(layer_path), "-i", str(audio_path)])

    filter_parts = []
    concat_inputs = []
    for idx in range(len(clips_to_process)):
        filter_parts.append(
            (
                f"[{idx}:v]"
                f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=decrease,"
                f"pad={WIDTH}:{HEIGHT}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={FPS}"
                f"[v{idx}]"
            )
        )
        concat_inputs.append(f"[v{idx}]")

    filter_parts.append(
        f"{''.join(concat_inputs)}concat=n={len(clips_to_process)}:v=1:a=0[base_video]"
    )
    filter_parts.append(f"[base_video][{layer_input_index}:v]overlay=x=0:y=0[final_video]")

    command.extend(
        [
            "-filter_complex",
            ";".join(filter_parts),
            "-map",
            "[final_video]",
            "-map",
            f"{audio_input_index}:a",
            "-c:v",
            "h264_videotoolbox",
            "-r",
            str(FPS),
            "-c:a",
            "aac",
            "-shortest",
            str(final_output_path),
        ]
    )
    return command


def main():
    """主执行函数。"""
    args = parse_args()
    if args.count is None:
        print_banner("VIDEO SPLICER STUDIO", "v9.5 Premium Terminal Edition")
        args.count = prompt_for_video_count()
    else:
        print_banner("VIDEO SPLICER STUDIO", "v9.5 Premium Terminal Edition")
    run_id = create_run_id()
    start_time = perf_counter()

    try:
        check_required_tools()
    except EnvironmentError as exc:
        print(exc)
        return

    for dir_path in [SOURCE_DIR, OUTPUT_DIR, SOURCE_LAYERS_DIR, SOURCE_AUDIOS_DIR]:
        dir_path.mkdir(parents=True, exist_ok=True)

    try:
        all_videos = load_media_files(SOURCE_DIR, {".mp4", ".mov"})
        all_layers = load_media_files(SOURCE_LAYERS_DIR, {".png"})
        all_audios = load_media_files(SOURCE_AUDIOS_DIR, {".aac"})
    except FileNotFoundError as exc:
        print(f"错误：无法访问素材文件夹: {exc}")
        return

    max_clips_needed = args.max_clips
    if not all_videos or len(all_videos) < max_clips_needed:
        print(f"错误：视频素材不足！请在 '{SOURCE_DIR}' 中至少放入 {max_clips_needed} 个视频文件。")
        return
    if not all_layers:
        print(f"错误：图片水印素材不足！请在 '{SOURCE_LAYERS_DIR}' 中至少放入一张 PNG 图片。")
        return
    if not all_audios:
        print(f"错误：音频素材不足！请在 '{SOURCE_AUDIOS_DIR}' 中至少放入一个 AAC 音频文件。")
        return

    print_result_line("info", "正在预读取所有视频时长...")
    duration_cache, invalid_videos = build_video_duration_cache(all_videos)
    if invalid_videos:
        print_result_line("warn", f"以下视频无法读取时长，已跳过：{', '.join(invalid_videos)}")

    if len(duration_cache) < max_clips_needed:
        print("错误：可用视频数量不足，无法满足单条视频的最大片段数需求。")
        return

    sorted_video_pool = build_sorted_video_pool(duration_cache)
    video_usage_counts = build_usage_counts(duration_cache.keys())
    layer_usage_counts = build_usage_counts(all_layers)
    audio_usage_counts = build_usage_counts(all_audios)

    print_section_title("任务总览")
    print_panel(
        "运行配置",
        [
            ("素材池", f"视频 {len(duration_cache)} 个 | 水印 {len(all_layers)} 个 | 音频 {len(all_audios)} 个"),
            ("生成目标", f"{args.count} 条 | 每条 {args.min_clips}-{args.max_clips} 个片段"),
            ("输出目录", str(OUTPUT_DIR)),
            ("启动模式", "未传 --count 时先询问生成条数"),
            ("快捷用法", "python3 video_splicer.py --count 40"),
        ],
        icon="✨",
    )

    successful_videos_count = 0
    attempt_count = 0

    while successful_videos_count < args.count and attempt_count < MAX_GENERATION_ATTEMPTS:
        attempt_count += 1
        clips_to_process = None
        selected_layer = None
        selected_audio = None

        try:
            total_duration = random.choice(POSSIBLE_DURATIONS)
            selected_layer, selected_audio = select_balanced_overlay_and_audio(
                layer_usage_counts,
                audio_usage_counts,
            )
            num_clips_for_this_video = random.randint(args.min_clips, args.max_clips)

            current_video_number = successful_videos_count + 1
            overall_percent = int((successful_videos_count / args.count) * 100)
            print_section_title(f"第 {current_video_number} / {args.count} 条")
            print_panel(
                "当前任务",
                [
                    ("尝试", f"第 {attempt_count} 次"),
                    (
                        "整体进度",
                        f"{build_progress_bar(overall_percent)} {overall_percent}% ({successful_videos_count} / {args.count})",
                    ),
                    (
                        "参数",
                        f"时长 {total_duration}s | 片段 {num_clips_for_this_video} | 水印 {selected_layer} | 音频 {selected_audio}",
                    ),
                ],
                icon="🎬",
            )

            random_durations = generate_random_durations(
                total_duration,
                num_clips_for_this_video,
                MIN_SEGMENT_DURATION,
            )

            print_result_line("stage", "正在智能匹配视频素材与所需时长...")
            clips_to_process = select_clips_for_video(
                random_durations,
                sorted_video_pool,
                video_usage_counts,
            )

            if clips_to_process is None:
                layer_usage_counts[selected_layer] -= 1
                audio_usage_counts[selected_audio] -= 1
                longest_failed_duration = max(random_durations)
                print_result_line(
                    "warn",
                    f"无法为最长 {longest_failed_duration:.2f} 秒的片段找到足够长的素材",
                )
                print_result_line("retry", "本次尝试失败，将继续使用新的随机参数重试")
                continue

            print_result_line("stage", "素材匹配成功，开始单次成片输出...")
            layer_path = SOURCE_LAYERS_DIR / selected_layer
            audio_path = SOURCE_AUDIOS_DIR / selected_audio
            final_output_path = build_output_path(run_id, current_video_number)
            command = build_single_pass_ffmpeg_command(
                clips_to_process,
                layer_path,
                audio_path,
                final_output_path,
            )
            run_ffmpeg_command(
                command,
                progress_label=f"第 {current_video_number} 条处理中",
                expected_duration=total_duration,
            )

            successful_videos_count += 1
            overall_percent = int((successful_videos_count / args.count) * 100)
            print_result_line("success", f"第 {current_video_number} 条视频制作成功")
            print_panel(
                "产出结果",
                [
                    ("文件", str(final_output_path)),
                    (
                        "当前总进度",
                        f"{build_progress_bar(overall_percent)} {overall_percent}% ({successful_videos_count} / {args.count})",
                    ),
                ],
                icon="📦",
            )

        except Exception as exc:
            if selected_layer is not None and layer_usage_counts[selected_layer] > 0:
                layer_usage_counts[selected_layer] -= 1
            if selected_audio is not None and audio_usage_counts[selected_audio] > 0:
                audio_usage_counts[selected_audio] -= 1
            rollback_video_usage(clips_to_process, video_usage_counts)
            print_result_line("error", f"本次尝试失败：{exc}")
            print_result_line("retry", "已回滚素材使用计数，继续下一次尝试")
            continue

    if successful_videos_count < args.count:
        total_elapsed = perf_counter() - start_time
        print_section_title("任务结束")
        print_result_line(
            "error",
            f"在 {MAX_GENERATION_ATTEMPTS} 次尝试后，仍只成功生成了 {successful_videos_count} 条视频",
        )
        print_panel(
            "结束摘要",
            [
                ("建议", "请补充更长的视频素材，或缩短目标总时长 / 减少片段数后重试"),
                ("累计耗时", format_elapsed_time(total_elapsed)),
            ],
            icon="⚠️",
        )
        print_divider()
        print_usage_summary("视频素材", video_usage_counts)
        print_usage_summary("水印素材", layer_usage_counts)
        print_usage_summary("音频素材", audio_usage_counts)
        return

    total_elapsed = perf_counter() - start_time
    print_section_title("任务完成")
    completion_percent = int((successful_videos_count / args.count) * 100)
    print_result_line("summary", "整轮生成任务已圆满完成")
    print_panel(
        "任务结算",
        [
            ("生成结果", f"成功生成 {successful_videos_count} 条视频，共尝试 {attempt_count} 次"),
            (
                "完成度",
                f"{build_progress_bar(completion_percent)} {completion_percent}% ({successful_videos_count} / {args.count})",
            ),
            ("累计耗时", format_elapsed_time(total_elapsed)),
            ("输出目录", str(OUTPUT_DIR)),
        ],
        icon="🏁",
    )
    print_divider()
    print_usage_summary("视频素材", video_usage_counts)
    print_usage_summary("水印素材", layer_usage_counts)
    print_usage_summary("音频素材", audio_usage_counts)


if __name__ == "__main__":
    main()
