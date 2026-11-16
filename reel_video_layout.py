"""Select the cheapest safe FFmpeg layout from actual displayed video dimensions.

Videos already formatted as vertical 9:16 do not need the doubled
background+foreground and boxblur pipeline. Source trimming, sound, 1080x1920
output, and caption burn-in are unchanged.
"""
import json
import math
import subprocess


def display_dimensions(probe):
    """FFprobe output; account for rotation recorded in display matrices."""
    streams = probe.get("streams", [])
    if len(streams) != 1:
        raise ValueError("VIDEO_DIMENSIONS_UNAVAILABLE")
    stream = streams[0]
    width, height = int(stream.get("width", 0)), int(stream.get("height", 0))
    if width < 1 or height < 1:
        raise ValueError("VIDEO_DIMENSIONS_INVALID")
    rotation = None
    for side_data in stream.get("side_data_list", []):
        if "rotation" in side_data:
            rotation = float(side_data["rotation"])
            break
    if rotation is None:
        rotation = float((stream.get("tags") or {}).get("rotate", 0))
    if not math.isfinite(rotation):
        raise ValueError("VIDEO_ROTATION_INVALID")
    if abs(round(rotation / 90)) % 2:
        width, height = height, width
    # Video sample aspect ratio is uncommon for mobile video. Respect non-square
    # pixels when deciding whether the displayed video is truly 9:16.
    sar = str(stream.get("sample_aspect_ratio") or "1:1")
    try:
        numerator, denominator = [int(x) for x in sar.split(":", 1)]
        if numerator > 0 and denominator > 0:
            ratio = width * numerator / (height * denominator)
        else:
            ratio = width / height
    except (ValueError, ZeroDivisionError):
        ratio = width / height
    return width, height, ratio


def ffprobe_layout(path, run=subprocess.run):
    output = run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_streams", "-of", "json", str(path)],
        capture_output=True, text=True, check=True, timeout=30)
    return display_dimensions(json.loads(output.stdout))


def layout_filter(width, height, ratio, blur_mode='auto'):
    if blur_mode not in ('auto', 'on', 'off'):
        raise ValueError('REEL_BLUR_MODE_INVALID')
    target_ratio = 9 / 16
    # A truly vertical Shorts/Reels source can be scaled straight to canvas.
    # Tolerance allows occasional slightly nonstandard mobile exports.
    if blur_mode != 'on' and height > width and abs(ratio - target_ratio) <= 0.007:
        return (
            ("format=yuv420p,setsar=1" if width == 1080 and height == 1920
             else "scale=1080:1920:flags=bicubic,setsar=1,format=yuv420p"),
            "native_vertical_no_blur"
        )
    if blur_mode == 'off':
        # No blur and no cropping. Preserve the foreground against a solid canvas.
        return ("scale=1080:1920:force_original_aspect_ratio=decrease,"
                "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=black,"
                "setsar=1,format=yuv420p", "solid_background_no_blur")
    return (
        "[0:v]split=2[bgsrc][fgsrc];"
        "[bgsrc]scale=270:480:force_original_aspect_ratio=increase,"
        "crop=270:480,setsar=1,boxblur=12:2,"
        "scale=1080:1920:flags=bicubic[bg];"
        "[fgsrc]scale=1080:1920:force_original_aspect_ratio=decrease,"
        "setsar=1[fg];"
        "[bg][fg]overlay=(W-w)/2:(H-h)/2:format=auto,"
        "format=yuv420p,setsar=1[v]",
        "letterbox_blurred_background"
    )
