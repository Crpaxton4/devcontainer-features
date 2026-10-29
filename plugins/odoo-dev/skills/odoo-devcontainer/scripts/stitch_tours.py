#!/usr/bin/env python3
"""Stitch the tour recordings into one walkthrough MP4 with chapter splash screens.

Reads ``chapters.yaml`` (next to this script, or the path given as first argument):
a title card, one splash screen + recording per chapter, an end card. The chapters
are embedded as MP4 chapter markers. Pillow renders the splash PNGs, ffmpeg encodes
the segments into the work directory and concatenates them.

Needs Pillow and PyYAML in the interpreter, a full ffmpeg and the Roboto fonts.

    python3 stitch_tours.py [chapters.yaml] [--work-dir DIR] [--font-dir DIR]
                            [--ffmpeg PATH] [--ffprobe PATH]

``width``, ``height`` and ``fps`` are optional keys of ``chapters.yaml``
(defaults 1366, 768, 25). The work directory defaults to a fresh temporary
directory, and the script prints its path.
"""
import argparse
import glob
import os
import subprocess
import sys
import tempfile
import textwrap
from datetime import datetime

import yaml
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_FONT_DIR = '/usr/share/fonts/truetype/roboto/unhinted/RobotoTTF'
BACKGROUND = '#1b1f27'
WHITE, GREY, LIGHT = '#ffffff', '#9aa3b2', '#d5d9e0'
ENCODE = ['-c:v', 'libx264', '-crf', '23', '-pix_fmt', 'yuv420p']

# Set by main() from the options and from chapters.yaml.
FONT_DIR = DEFAULT_FONT_DIR
WIDTH, HEIGHT, FPS = 1366, 768, 25
FFMPEG, FFPROBE = 'ffmpeg', 'ffprobe'


def font(name, size):
    return ImageFont.truetype(os.path.join(FONT_DIR, 'Roboto-%s.ttf' % name), size)


def run(cmd):
    subprocess.run(cmd, check=True)


def ffprobe_duration(path):
    out = subprocess.run(
        [FFPROBE, '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', path],
        check=True, stdout=subprocess.PIPE, universal_newlines=True).stdout
    return float(out.strip())


def draw_centered_lines(draw, lines, fnt, color, y, spacing=12):
    for line in lines:
        x0, y0, x1, y1 = draw.textbbox((0, 0), line, font=fnt)
        draw.text(((WIDTH - (x1 - x0)) / 2, y), line, font=fnt, fill=color)
        y += (y1 - y0) + spacing
    return y


def render_splash(path, number, total, title, description):
    image = Image.new('RGB', (WIDTH, HEIGHT), BACKGROUND)
    draw = ImageDraw.Draw(image)
    y = 190
    y = draw_centered_lines(draw, ['Chapter %d of %d' % (number, total)], font('Regular', 28), GREY, y, 30)
    title_lines = textwrap.wrap(title, 34)
    y = draw_centered_lines(draw, title_lines, font('Bold', 56), WHITE, y, 14)
    y += 30
    desc_lines = textwrap.wrap(description, 62)
    draw_centered_lines(draw, desc_lines, font('Light', 32), LIGHT, y, 14)
    image.save(path)


def render_card(path, title, subtitle=None):
    image = Image.new('RGB', (WIDTH, HEIGHT), BACKGROUND)
    draw = ImageDraw.Draw(image)
    y = 300 if subtitle else 340
    y = draw_centered_lines(draw, textwrap.wrap(title, 34), font('Bold', 64), WHITE, y, 14)
    if subtitle:
        y += 30
        draw_centered_lines(draw, textwrap.wrap(subtitle, 60), font('Light', 32), LIGHT, y, 14)
    image.save(path)


def encode_still(png, mp4, seconds, fade):
    run([FFMPEG, '-loglevel', 'error', '-y', '-loop', '1', '-framerate', str(FPS), '-t', str(seconds),
         '-i', png, '-vf', 'fade=t=in:st=0:d=%s,fade=t=out:st=%s:d=%s' % (fade, seconds - fade, fade),
         *ENCODE, mp4])
    return float(seconds)


def encode_tour(src, mp4, hold, fade):
    duration = ffprobe_duration(src) + hold
    run([FFMPEG, '-loglevel', 'error', '-y', '-i', src, '-vf',
         'tpad=stop_mode=clone:stop_duration=%s,fade=t=in:st=0:d=%s,fade=t=out:st=%s:d=%s'
         % (hold, fade, duration - fade, fade), *ENCODE, mp4])
    return ffprobe_duration(mp4)


def find_recording(chapter, recordings_dir):
    if chapter.get('file'):
        return chapter['file']
    candidates = sorted(glob.glob(os.path.join(recordings_dir, '%s_*.mp4' % chapter['tour'])))
    if not candidates:
        sys.exit('No recording of %s in %s' % (chapter['tour'], recordings_dir))
    return candidates[-1]


def parse_args(argv):
    parser = argparse.ArgumentParser(description='Stitch tour recordings into one walkthrough MP4.')
    parser.add_argument('config', nargs='?', default=os.path.join(HERE, 'chapters.yaml'))
    parser.add_argument('--work-dir', help='directory for the segments (default: a new temporary directory)')
    parser.add_argument('--font-dir', default=DEFAULT_FONT_DIR)
    parser.add_argument('--ffmpeg', default='ffmpeg')
    parser.add_argument('--ffprobe', default='ffprobe')
    return parser.parse_args(argv)


def main(argv=None):
    global FONT_DIR, WIDTH, HEIGHT, FPS, FFMPEG, FFPROBE
    args = parse_args(sys.argv[1:] if argv is None else argv)
    with open(args.config) as f:
        cfg = yaml.safe_load(f)
    FONT_DIR, FFMPEG, FFPROBE = args.font_dir, args.ffmpeg, args.ffprobe
    WIDTH, HEIGHT, FPS = cfg.get('width', 1366), cfg.get('height', 768), cfg.get('fps', 25)
    chapters = cfg['chapters']
    total = len(chapters)
    splash_s, hold_s, fade_s = cfg['splash_seconds'], cfg['hold_seconds'], cfg['fade_seconds']
    work = args.work_dir or tempfile.mkdtemp(prefix='stitch-tours-')
    os.makedirs(work, exist_ok=True)
    print('Work directory: %s' % work)

    segments = []  # (mp4, duration)
    markers = []   # (start, end, title)

    png = os.path.join(work, 'title.png')
    render_card(png, cfg['title'], cfg.get('subtitle'))
    mp4 = os.path.join(work, 'title.mp4')
    segments.append((mp4, encode_still(png, mp4, splash_s, fade_s)))

    for number, chapter in enumerate(chapters, 1):
        src = find_recording(chapter, cfg['recordings_dir'])
        print('Chapter %2d: %s  <-  %s' % (number, chapter['title'], os.path.basename(src)))
        png = os.path.join(work, 'ch%02d_splash.png' % number)
        render_splash(png, number, total, chapter['title'], chapter['description'])
        start = sum(d for _, d in segments)
        mp4 = os.path.join(work, 'ch%02d_splash.mp4' % number)
        segments.append((mp4, encode_still(png, mp4, splash_s, fade_s)))
        mp4 = os.path.join(work, 'ch%02d_tour.mp4' % number)
        segments.append((mp4, encode_tour(src, mp4, hold_s, fade_s)))
        markers.append((start, sum(d for _, d in segments), chapter['title']))

    png = os.path.join(work, 'end.png')
    render_card(png, cfg['end_card'])
    mp4 = os.path.join(work, 'end.mp4')
    segments.append((mp4, encode_still(png, mp4, splash_s, fade_s)))

    list_path = os.path.join(work, 'list.txt')
    with open(list_path, 'w') as f:
        for mp4, _ in segments:
            f.write("file '%s'\n" % mp4)
    meta_path = os.path.join(work, 'chapters.ffmeta')
    with open(meta_path, 'w') as f:
        f.write(';FFMETADATA1\ntitle=%s\n' % cfg['title'])
        for start, end, title in markers:
            f.write('[CHAPTER]\nTIMEBASE=1/1000\nSTART=%d\nEND=%d\ntitle=%s\n'
                    % (round(start * 1000), round(end * 1000), title))

    output = cfg['output'].format(date=datetime.now().strftime('%Y%m%d'))
    run([FFMPEG, '-loglevel', 'error', '-y', '-f', 'concat', '-safe', '0', '-i', list_path,
         '-i', meta_path, '-map_metadata', '1', *ENCODE, '-movflags', '+faststart', output])

    duration = ffprobe_duration(output)
    print('\nChapters:')
    for start, _, title in markers:
        print('  %02d:%02d  %s' % (divmod(int(start), 60) + (title,)))
    print('\nOutput: %s\nDuration: %d:%02d\nSize: %.1f MB'
          % (output, *divmod(int(duration), 60), os.path.getsize(output) / 1e6))


if __name__ == '__main__':
    main()
