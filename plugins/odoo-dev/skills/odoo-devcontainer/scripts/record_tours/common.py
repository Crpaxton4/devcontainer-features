"""Series-independent part of record-tours.

Option parsing, the series registry, the caption-bar CSS, the ffmpeg encode and
the GIF pass. A series-specific module (``odoo17.py``) supplies the patches of
the Odoo internals and calls into this module.

Python 3.9 syntax only: the recorder runs under the project venv interpreter.
"""
import argparse
import base64
import os
import re
import shutil
import subprocess
import time
from datetime import datetime

# Same shape as SERIES_RE in bump_manifest_version.py: <major>.0
SERIES_RE = re.compile(r'^(\d+)\.0$')

# Series -> module that implements the patches for it. A series that is not a
# key here has no implementation, and record-tours refuses to run on it.
IMPLEMENTATIONS = {
    '17.0': 'record_tours.odoo17',
    '18.0': 'record_tours.odoo17',
}

DEFAULT_GIF_FILTER = 'fps=6,scale=1024:-1'

# The page script, including the caption bar CSS. A ``%`` format string: the
# arguments are the module path of the tour pointer, the tour name and the JSON
# options. Every literal ``%`` is doubled (``calc(100%% - ...)``).
POINTER_PATCH = """
(function () {
    const mod = odoo.loader.modules.get("%s");
    if (mod && mod.TourPointer) {
        Object.defineProperty(mod.TourPointer.prototype, "isOpen", {
            get: () => true, configurable: true,
        });
    }
    // Caption bar: mirrors the text of the current tour step at the bottom of the viewport.
    const BAR_HEIGHT = "72px";
    const style = document.createElement("style");
    style.textContent = `
        #o_record_caption { position: fixed; left: 0; right: 0; bottom: 0; height: ${BAR_HEIGHT};
            z-index: 1100; background: #1b1f27; color: #fff; font: 600 26px Roboto, sans-serif;
            display: flex; align-items: center; padding: 0 32px; box-sizing: border-box;
            white-space: nowrap; overflow: hidden; }
        .o_web_client, .modal { height: calc(100%% - ${BAR_HEIGHT}) !important; }
    `;
    document.head.appendChild(style);
    const bar = document.createElement("div");
    bar.id = "o_record_caption";
    document.body.appendChild(bar);
    const update = () => {
        const content = document.querySelector(".o_tour_pointer_content");
        const text = content ? content.textContent.trim() : "";
        if (text && bar.textContent !== text) {
            bar.textContent = text;
        }
    };
    new MutationObserver(update).observe(document.body, {
        childList: true, subtree: true, characterData: true,
    });
    return odoo.startTour('%s', %s);
})()
"""


class SeriesError(Exception):
    """ODOO_VERSION is unset, malformed or has no implementation."""


def select_implementation(version):
    """Return the module name that serves ``version``, or raise SeriesError."""
    version = (version or '').strip()
    if not version:
        raise SeriesError(
            'ODOO_VERSION is not set. Set it to the Odoo series, for example 17.0.')
    if not SERIES_RE.match(version):
        raise SeriesError(
            "ODOO_VERSION '%s' is not an Odoo series. Expected <major>.0, for example 17.0."
            % version)
    if version not in IMPLEMENTATIONS:
        raise SeriesError(
            'record-tours has no implementation for Odoo series %s. Supported series: %s.'
            % (version, ', '.join(sorted(IMPLEMENTATIONS))))
    return IMPLEMENTATIONS[version]


def build_parser():
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--step-delay', type=int, default=1000)
    parser.add_argument('--pointer-duration', type=int, default=2000)
    parser.add_argument('--ffmpeg')
    parser.add_argument('--no-gif', dest='gif', action='store_false')
    parser.add_argument('--gif-filter', default=DEFAULT_GIF_FILTER)
    parser.add_argument('--timeout', type=int, default=900)
    parser.add_argument('--python')
    return parser


def parse_args(argv):
    """Split ``argv`` into the recorder settings and the arguments for odoo."""
    settings, odoo_args = build_parser().parse_known_args(argv)
    settings.ffmpeg = settings.ffmpeg or shutil.which('ffmpeg')
    return settings, odoo_args


def venv_python(settings):
    """The interpreter to re-exec under, or None to keep the current one."""
    return settings.python or os.environ.get('ODOO_VENV_PYTHON') or None


def pointer_code(pointer_module, tour_name, options_json):
    return POINTER_PATCH % (pointer_module, tour_name, options_json)


def save_recording(browser, prefix, settings):
    browser.stop_screencast()

    for f in browser.screencast_frames:
        with open(f['file_path'], 'rb') as b64_file:
            frame = base64.decodebytes(b64_file.read())
        os.unlink(f['file_path'])
        f['file_path'] = f['file_path'].replace('.b64', '.png')
        with open(f['file_path'], 'wb') as png_file:
            png_file.write(frame)

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    outfile = os.path.join(browser.screencasts_dir, '%s_%s.mp4' % (prefix, timestamp))

    if not settings.ffmpeg:
        frames_dir = outfile[:-len('.mp4')]
        shutil.move(browser.screencasts_frames_dir, frames_dir)
        browser._logger.warning('No ffmpeg found, recording frames in: %s', frames_dir)
        browser.screencast_frames = []
        return

    frames = browser.screencast_frames
    concat_script_path = outfile[:-len('.mp4')] + '.txt'
    with open(concat_script_path, 'w') as concat_file:
        for i, frame in enumerate(frames):
            end_time = time.time() if i == len(frames) - 1 else frames[i + 1]['timestamp']
            duration = end_time - frame['timestamp']
            concat_file.write("file '%s'\nduration %s\n" % (frame['file_path'], duration))
        concat_file.write("file '%s'" % frames[-1]['file_path'])  # needed by the concat demuxer

    try:
        subprocess.run([
            settings.ffmpeg, '-loglevel', 'error', '-y', '-f', 'concat', '-safe', '0',
            '-i', concat_script_path, '-vf', 'pad=ceil(iw/2)*2:ceil(ih/2)*2',
            '-pix_fmt', 'yuv420p', '-g', '0', outfile,
        ], check=True)
    except subprocess.CalledProcessError:
        browser._logger.error('Failed to encode the recording %s', outfile)
    else:
        os.unlink(concat_script_path)
        browser._logger.info('Recording in: %s', outfile)
        if settings.gif:
            gif = outfile[:-len('.mp4')] + '.gif'
            try:
                subprocess.run([
                    settings.ffmpeg, '-loglevel', 'error', '-y', '-i', outfile, '-vf',
                    '%s:flags=lanczos,split[a][b];[a]palettegen[p];[b][p]paletteuse'
                    % settings.gif_filter,
                    gif,
                ], check=True)
            except subprocess.CalledProcessError:
                browser._logger.error('Failed to encode the GIF %s', gif)
            else:
                browser._logger.info('GIF in: %s', gif)
    browser.screencast_frames = []
