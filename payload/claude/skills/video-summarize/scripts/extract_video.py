#!/usr/bin/env python3
"""
Extract metadata, transcript (auto-captions), and evenly-spaced frames
from a video URL (any site yt-dlp supports) or a local video file.

Prints a single JSON manifest to stdout describing everything written
to disk, so the caller (Claude) can Read the transcript and frames.

Usage:
    python extract_video.py <url_or_path> [--out DIR] [--max-frames N]
"""
import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", **kw)


def is_url(s: str) -> bool:
    return re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", s) is not None


def vtt_to_text(vtt_path: Path) -> str:
    lines = vtt_path.read_text(encoding="utf-8", errors="replace").splitlines()
    out, prev = [], None
    for line in lines:
        line = line.strip()
        if not line or line == "WEBVTT":
            continue
        if "-->" in line or line.isdigit():
            continue
        # strip inline vtt tags like <00:00:01.000><c> word</c>
        line = re.sub(r"<[^>]+>", "", line)
        if line and line != prev:
            out.append(line)
            prev = line
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source")
    ap.add_argument("--out", default=None)
    ap.add_argument("--max-frames", type=int, default=16)
    args = ap.parse_args()

    src = args.source
    outdir = Path(args.out) if args.out else Path.cwd() / "video_summary_out"
    frames_dir = outdir / "frames"
    outdir.mkdir(parents=True, exist_ok=True)
    frames_dir.mkdir(parents=True, exist_ok=True)

    manifest = {
        "source": src,
        "title": None,
        "uploader": None,
        "duration_sec": None,
        "description": None,
        "transcript_path": None,
        "video_path": None,
        "frames": [],
        "errors": [],
    }

    video_path = None
    duration = None

    if is_url(src):
        if not shutil.which("yt-dlp"):
            manifest["errors"].append("yt-dlp not found on PATH")
            print(json.dumps(manifest, indent=2))
            return 1

        # 1) metadata
        r = run(["yt-dlp", "--no-warnings", "-J", src])
        if r.returncode == 0 and r.stdout.strip():
            try:
                info = json.loads(r.stdout)
                manifest["title"] = info.get("title")
                manifest["uploader"] = info.get("uploader")
                manifest["duration_sec"] = info.get("duration")
                desc = info.get("description") or ""
                manifest["description"] = desc[:2000]
                duration = info.get("duration")
            except json.JSONDecodeError:
                manifest["errors"].append("failed to parse yt-dlp -J output")
        else:
            manifest["errors"].append(f"yt-dlp metadata fetch failed: {r.stderr[-500:]}")

        # 2) subtitles (auto or manual), prefer English
        sub_base = outdir / "sub"
        run([
            "yt-dlp", "--no-warnings", "--skip-download",
            "--write-auto-sub", "--write-sub",
            "--sub-lang", "en.*,en",
            "--convert-subs", "vtt",
            "-o", str(sub_base) + ".%(ext)s",
            src,
        ])
        vtt_files = list(outdir.glob("sub*.vtt"))
        if vtt_files:
            text = vtt_to_text(vtt_files[0])
            transcript_path = outdir / "transcript.txt"
            transcript_path.write_text(text, encoding="utf-8")
            manifest["transcript_path"] = str(transcript_path)
        else:
            manifest["errors"].append("no captions available for this video")

        # 3) low-res video for frame sampling
        video_template = outdir / "video.%(ext)s"
        r = run([
            "yt-dlp", "--no-warnings",
            "-f", "bv*[height<=480]+ba/b[height<=480]/worst",
            "-o", str(video_template),
            src,
        ])
        candidates = [p for p in outdir.glob("video.*") if p.suffix != ".vtt"]
        if candidates:
            video_path = candidates[0]
            manifest["video_path"] = str(video_path)
        else:
            manifest["errors"].append(f"video download failed: {r.stderr[-500:]}")
    else:
        p = Path(src)
        if not p.exists():
            manifest["errors"].append(f"local file not found: {src}")
            print(json.dumps(manifest, indent=2))
            return 1
        video_path = p
        manifest["video_path"] = str(p)
        manifest["title"] = p.stem

    if video_path and shutil.which("ffprobe") and duration is None:
        r = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                  "-of", "default=noprint_wrappers=1:nokey=1", str(video_path)])
        try:
            duration = float(r.stdout.strip())
            manifest["duration_sec"] = duration
        except ValueError:
            pass

    if video_path and shutil.which("ffmpeg"):
        max_frames = max(1, args.max_frames)
        dur = duration or 60.0
        interval = max(dur / max_frames, 2.0)
        r = run([
            "ffmpeg", "-y", "-i", str(video_path),
            "-vf", f"fps=1/{interval}",
            "-vsync", "vfr", "-q:v", "3",
            str(frames_dir / "frame_%03d.jpg"),
        ])
        frame_files = sorted(frames_dir.glob("frame_*.jpg"))[:max_frames]
        manifest["frames"] = [str(f) for f in frame_files]
        if not frame_files:
            manifest["errors"].append(f"frame extraction produced no output: {r.stderr[-500:]}")
    else:
        manifest["errors"].append("ffmpeg not found on PATH or no video to sample")

    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
