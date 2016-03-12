import os


def out_str_builder(i):
    return 'rtmp://127.0.0.1:1935/cams/source{}'.format(i)


class AppConfiguration:
    BASE_DIR = os.path.realpath(os.path.dirname(__file__))
    STATIC_DIR = os.path.join(BASE_DIR, 'static')
    FFMPEG_PATH = r'D:\ffmpeg-20160307-git-6f5048f-win64-static\bin\ffmpeg.exe'
    FFMPEG_GLOBAL_ARGS = ['-hide_banner', '-nostats', ]
    FFMPEG_OUT_STR_BUILDER = out_str_builder
    FFPROBE_PATH = r'D:\ffmpeg-20160307-git-6f5048f-win64-static\bin\ffprobe.exe'
    FFPROBE_ARGS = ['-hide_banner']
    FFPROBE_TIMEOUT = 5
    LAYOUT_MAP_WIDTH = 12
    EBUR_STATS_FILENAME_TPL = 'D:\Temp\ebur.source{}.stats'
    FFMPEG_OUT_ARGS = ['-f', 'flv',
                       '-c:v', 'libx264', '-g', '25', '-preset', 'fast',
                       '-c:a', 'aac', '-b:a', '128k']
    LOG_DIR = r'D:\Temp'
