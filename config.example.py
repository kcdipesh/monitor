import os


class AppConfiguration:

    BASE_DIR = os.path.realpath(os.path.dirname(__file__))
    STATIC_DIR = os.path.join(BASE_DIR, 'static')
    FFMPEG_PATH = r'C:\ffmpeg-20160301-git-1c7e2cf-win64-static\bin\ffmpeg.exe'
    FFMPEG_GLOBAL_ARGS = ['-hide_banner', '-nostats', ]
    FFPROBE_PATH = r'C:\ffmpeg-20160301-git-1c7e2cf-win64-static\bin\ffprobe.exe'
    FFPROBE_ARGS = ['-hide_banner']
    FFPROBE_TIMEOUT = 5
    AUDIO_METER_CHANNELS = 2
    LAYOUT_MAP_WIDTH = 6
