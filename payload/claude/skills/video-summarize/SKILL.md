---
name: video-summarize
description: Use this skill when the user wants a video summarized, transcribed, or analyzed from a URL (YouTube or any site yt-dlp supports) or a local video file — trigger phrases include "summarize this video", "extract this video and summarize it", "what happens in this video", "watch this and tell me", or pasting a video link and asking what it's about. Combines the auto-generated transcript with sampled video frames (read visually) so the summary covers both what was said and what was shown, and can follow up with a web search for related context.
version: 1.0.0
---

# Video Summarize

Extracts a transcript and evenly-spaced frames from a video (any URL yt-dlp
supports, or a local file), then produces a summary grounded in both the
spoken/caption content and what's visually on screen — not just captions
alone, since captions miss on-screen text, demos, slides, and visual gags.

## Workflow

1. **Run the extractor** with the video URL or local path:
   ```
   python "__HOMEBS__\.claude\skills\video-summarize\scripts\extract_video.py" "<url_or_path>" --out "<scratchpad>/video_summary_out" --max-frames 16
   ```
   Use the session's scratchpad directory for `--out`. Raise `--max-frames`
   for long/dense videos (e.g. 24), lower it for short clips (e.g. 8).

2. **Parse the JSON manifest** printed to stdout. It contains: `title`,
   `uploader`, `duration_sec`, `description`, `transcript_path` (or null if
   no captions existed), `video_path`, `frames` (list of frame image paths),
   and `errors` (non-fatal issues — e.g. "no captions available" is common
   and not a failure, frame-based summary still works).

3. **Read the transcript** at `transcript_path` with the Read tool, if present.

4. **Read each frame image** in `frames` with the Read tool (they're plain
   JPEGs — Read renders them visually). Look for: on-screen text/slides,
   scene changes, UI/demo steps, charts, people, setting changes.

5. **Synthesize one summary** that merges both signals — don't just
   paraphrase the transcript. Call out things visible in frames but absent
   from captions (e.g. "the presenter shows a chart at ~4:30 comparing X
   and Y" when captions never mention numbers). Structure for a long video:
   - One-paragraph overview (what it is, who made it, runtime)
   - Timeline/key-moments list anchored to approximate timestamps
   - Anything notable purely visual (demos, on-screen code, slides)

6. **Optional — "search it"**: if the user's request implies wanting
   external context (fact-checking a claim, finding the product/paper/repo
   shown, background on the creator), run a WebSearch on the extracted
   title/topic and fold relevant findings into the summary, citing sources.

7. **Clean up**: the extracted video/frames live under scratchpad and are
   ephemeral — no need to delete them explicitly, but mention to the user
   that raw files aren't kept anywhere permanent unless they ask to save
   the video itself.

## Notes

- Works on any site yt-dlp supports (YouTube, Twitter/X, Vimeo, Reddit,
  TikTok, direct .mp4 links, etc.), not just YouTube.
- Downloads capped at 480p — this is for frame sampling, not archival
  quality. If the user wants the actual video file kept, say so explicitly
  and copy it out of scratchpad.
- If both transcript and frame extraction fail (e.g. private/DRM'd video,
  geo-blocked), report the specific `errors` from the manifest rather than
  guessing at content.
- Long videos: don't Read all frames if there are many — max-frames already
  caps this, but for a 3-hour stream consider sampling in two passes
  (overview pass at sparse frames, then a denser pass on a specific
  timeframe the user cares about) rather than blowing through context.
