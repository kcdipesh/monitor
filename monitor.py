import argparse
import json
import os
import subprocess
import sys
import math
import threading
import datetime
import re
from collections import deque

from config import AppConfiguration

VERSION = "dev"
EBUR_RE = re.compile(r'\[Parsed_ebur128_.+M:\s*(?P<m>\S+)\s+S:\s*(?P<s>\S+)\s+I:\s*(?P<i>\S+).+LRA:\s*(?P<lra>\S+)')


class ConfException(Exception):
    pass


class LayoutException(Exception):
    pass


class FrameInputException(Exception):
    pass


def _ffmpeg_thread(exec_args, log_path, ebur_stats_path, audio_ch_ids):

        deque_size = 5

        def _write_log(s):
            with open(log_path, 'a') as fout:
                fout.write("{}: {}\n".format(datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'), s))

        def _write_ebur_stats(stats):
            with open(ebur_stats_path, 'w') as fout:
                for s in stats:
                    fout.write('{}\n'.format(s))

        while True:
            _write_log('Starting ffmpeg process {}'.format(' '.join(exec_args)))
            last_lines_log = deque(maxlen=deque_size)
            ebur_stats_disabled = False
            proc = subprocess.Popen(exec_args, stderr=subprocess.PIPE, universal_newlines=True)
            _write_log('ffmpeg process started')
            audio_ch_count = len(audio_ch_ids)
            gathered_ebur_stats = []
            current_ch = 0
            passed_summary = False
            for line in proc.stderr:
                last_lines_log.append(line)
                if not ebur_stats_disabled:
                    if line.startswith('[Parsed_ebur128_'):
                        if not passed_summary and line.find('Summary') != -1:
                            continue
                        m = EBUR_RE.match(line)
                        if m is not None:
                            passed_summary = True
                            gathered_ebur_stats.append('{} {}'.format(audio_ch_ids[current_ch], ' '.join(m.groups())))
                            current_ch += 1
                        else:
                            try:
                                os.remove(ebur_stats_path)
                            except FileNotFoundError:
                                pass
                            _write_log('<WARNING> EBUR stats parsing error - disabling.')
                            ebur_stats_disabled = True
                        if current_ch == audio_ch_count:
                            _write_ebur_stats(gathered_ebur_stats)
                            gathered_ebur_stats = []
                            current_ch = 0
            _write_log(
                'ffmpeg process stopped (code {}). Output:\n"{}".'.format(proc.returncode, '\n'.join(last_lines_log))
            )


class Application:
    def __init__(self, conf, parsed_args):
        self.conf = conf
        self.verbosity = parsed_args.verbosity
        self.command = parsed_args.command
        self.args = parsed_args

        self.layout = None
        self.layout_source_info = None

    def _error(self, msg):
        sys.stderr.write('<ERROR> {}\n'.format(msg))
        sys.exit(1)

    def _log(self, msg):
        if self.verbosity >= 1:
            sys.stdout.write('{}\n'.format(msg))
            sys.stdout.flush()

    def _info(self, msg):
        if self.verbosity >= 2:
            sys.stdout.write('<INFO> {}\n'.format(msg))
            sys.stdout.flush()

    def _warning(self, msg):
        sys.stderr.write('<WARNING> {}\n'.format(msg))
        sys.stderr.flush()

    def exec(self):
        if self.command is None:
            self._error('Command is missing - see "monitor.py -h" for usage details.')
        try:
            cmd_callable = getattr(self, '_cmd_{}'.format(self.command))
        except AttributeError:
            self._error('Unknown command: "{}".'.format(self.command))
        else:
            cmd_callable()

    def _cmd_confcheck(self):
        try:
            self._conf_check()
        except ConfException as e:
            self._error(str(e))
        else:
            self._log('OK')

    def _cmd_run(self):
        try:
            self._conf_check()
        except ConfException as e:
            self._error(str(e))
        try:
            self._layout_check()
        except LayoutException as e:
            self._error(str(e))
        ffmpeg_threads = []
        for (i, ls) in enumerate(map(self._get_source_info, [f['source'] for f in self.layout])):
            audio_streams = []
            video_streams = []
            for s in ls['streams']:
                if s['codec_type'] == 'video':
                    self._info('Source {} - found video stream #{}.'.format(i, s['index']))
                    video_streams.append(s)
                elif s['codec_type'] == 'audio':
                    self._info('Source {} - found audio stream #{}.'.format(i, s['index']))
                    audio_streams.append(s)
            video_height = self.layout[i]['video_height']
            graph, meter_ratio, audio_channel_ids = self._get_meter_graph(
                audio_streams, self.layout[i]['meter_channel_font'], self.layout[i]['meter_channel_font_size']
            )
            self._info('Scaling source video...')
            vs = video_streams[0]
            try:
                l, r = str(vs['sample_aspect_ratio']).split(':')
            except ValueError:
                self._warning('SAR error - using SAR = 1.')
                sar = 1
            else:
                sar = float(l) / float(r)
                self._info('Source SAR = {}.'.format(sar))
            eff_source_video_width = math.trunc(vs['width'] * sar)
            source_video_height = vs['height']
            scale_factor = video_height / source_video_height
            self._info('Scale factor: {}.'.format(scale_factor))
            video_width = math.trunc(eff_source_video_width * scale_factor)
            self._info('Scaled video size: {w}x{h}.'.format(w=video_width, h=video_height))
            scale_chain = "[v:0]scale={w}:{h},setsar=sar=1[scaled_video]".format(w=video_width, h=video_height)
            graph.append(scale_chain)
            self._info('Drawing border...')
            border_width = 2
            border_color = '0x00FF00'
            border_chain = "color=c={border_color}:s={w}x{h}[border_bg];" \
                           "[border_bg][scaled_video]overlay={bw}:{bw}[bordered_video]" \
                           "".format(border_color=border_color, w=border_width * 2 + video_width,
                                     h=border_width * 2 + video_height, bw=border_width)
            self._info('Border chains: "{}".'.format(border_chain))
            graph.append(border_chain)
            self._info('Scale chain: "{}".'.format(scale_chain))
            self._info('Calculating audio meter size...')
            self._info('Audio meter ratio: {}.'.format(meter_ratio))
            meter_width = math.trunc(video_height * meter_ratio)
            meter_height = border_width * 2 + video_height
            self._info('Scaled audio meter size: {w}x{h}.'.format(w=meter_width, h=video_height))
            meter_scale_chain = "[all_meters_out]scale=w={w}:h={h}[scaled_meters]".format(w=meter_width, h=meter_height)
            self._info('Audio meter scale chain: "{}".'.format(meter_scale_chain))
            graph.append(meter_scale_chain)
            total_width = border_width * 2 + video_width + 2 + meter_width
            total_height = meter_height
            chain = "color=c=black:s={bg_width}x{bg_height}[main_bg];" \
                    "[main_bg][scaled_meters]overlay={meters_x_offset}:0[main_mid_0];" \
                    "[main_mid_0][bordered_video]overlay=0:0[video_out]" \
                    "".format(bg_width=total_width, bg_height=total_height,
                              meters_x_offset=border_width * 2 + video_width + 2)
            self._info('Overlay chains: "{}".'.format(chain))
            graph.append(chain)
            graph_str = ';'.join(graph)
            self._info('Filtergraph ready: "{}".'.format(graph_str))
            exec_args = [self.conf.FFMPEG_PATH] + self.conf.FFMPEG_GLOBAL_ARGS + ['-i', self.layout[i]['source']] + \
                        ['-filter_complex', graph_str, '-map', 'a:0', '-map', '[video_out]'] + \
                        self.conf.FFMPEG_OUT_ARGS + [self.conf.FFMPEG_OUT_STR_BUILDER(i)]
            self._info('Args: {}'.format(exec_args))
            log_path = os.path.join(self.conf.LOG_DIR, 'monitor.source{}.log'.format(i))
            self._info('Creating thread #{}...'.format(i))
            ebur_stats_path = self.conf.EBUR_STATS_FILENAME_TPL.format(i)
            thread = threading.Thread(
                target=_ffmpeg_thread,
                args=(exec_args, log_path, ebur_stats_path, audio_channel_ids )
            )
            ffmpeg_threads.append(thread)
        self._info('Starting ffmpeg threads...')
        for i, t in enumerate(ffmpeg_threads):
            t.start()
            self._info('ffmpeg thread #{} started.'.format(i))

    def _get_meter_graph(self, audio_streams, channel_label_font, channel_label_font_size):
        self._info('Building audio meter graph...')
        graph = []
        # scale
        scale_width = 24
        scale_height = 456
        scale_x = 8
        scale_y = 22
        self._info('Drawing scale...')
        scale_graph = "anullsrc, ebur128=video=1:meter=18[ebur_nullsrc],anullsink;" \
                      "[ebur_nullsrc]crop={w}:{h}:{x}:{y}[ebur_scale]" \
                      "".format(w=scale_width, h=scale_height, x=scale_x, y=scale_y)
        self._info('Scale chains: "{}".'.format(scale_graph))
        graph.append(scale_graph)
        audio_in_chains = []
        audio_splitted_channels = []
        self._info('Splitting audio channels...')
        for s in audio_streams:
            if s['channels'] == 1:
                audio_in_chains.append("[0:{s_id}]anull[{output_name}]".format(
                    s_id=s['index'], output_name="audio_in_{}_0".format(s['index'])
                ))
                audio_splitted_channels.append("{}_0".format(s['index']))
            else:
                splitted_outputs = ["{}_{}".format(s['index'], ch_id) for ch_id in range(0, s['channels'])]
                chain = "[0:{s_id}]channelsplit=channel_layout={ch_layout}{outputs}".format(
                    s_id=s['index'], ch_layout=s['channel_layout'],
                    outputs=''.join(["[audio_in_{}]".format(o) for o in splitted_outputs])
                )
                audio_in_chains.append(chain)
                audio_splitted_channels.extend(splitted_outputs)
        self._info('Audio inputs chains: "{}".'.format(';'.join(audio_in_chains)))
        graph.extend(audio_in_chains)
        audio_meter_chains = []
        meter_width = 22
        meter_crop_width = 20
        meter_crop_height = 432
        meter_x = 612
        meter_y = 40
        meter_y_offset = 18
        meter_label_y_offset = 4
        self._info('Drawing audio meters...')
        for c in audio_splitted_channels:
            chain = "color=c=black:s={meter_width}x{scale_height}[meter_bg_{ch}];" \
                    "[audio_in_{ch}]ebur128=meter=18:video=1:framelog=info[ebur_{ch}], anullsink;" \
                    "[ebur_{ch}]crop={meter_crop_width}:{meter_crop_height}:{meter_x}:{meter_y}[ebur_crop_{ch}];" \
                    "[meter_bg_{ch}][ebur_crop_{ch}]overlay=0:{meter_y_offset}," \
                    "drawtext=fontcolor=0xF0F0F0:fontfile='{font}':fontsize={fontsize}:text='{text}':" \
                    "x=0:y={meter_label_y_offset}[meter_{ch}]" \
                    "".format(ch=c, font=self._escape_str(channel_label_font), fontsize=channel_label_font_size,
                              text=c.replace('_', r'\:'), meter_width=meter_width, scale_height=scale_height,
                              meter_crop_width=meter_crop_width, meter_crop_height=meter_crop_height, meter_x=meter_x,
                              meter_y=meter_y, meter_y_offset=meter_y_offset, meter_label_y_offset=meter_label_y_offset)
            audio_meter_chains.append(chain)
        self._info('Audio meters chains: "{}".'.format(';'.join(audio_meter_chains)))
        graph.extend(audio_meter_chains)
        total_meters_width = scale_width + 2 + len(audio_meter_chains) * meter_width
        meters_ratio = total_meters_width / scale_height
        self._info('Combining audio meters...')
        bg_chain = "color=c=black:s={total_meters_width}x{scale_height}[all_meters_bg];" \
                   "[all_meters_bg][ebur_scale]overlay=0:0[all_meters_mid_0]" \
                   "".format(total_meters_width=total_meters_width, scale_height=scale_height)
        self._info('Audio meters background chains: "{}".'.format(bg_chain))
        overlay_chains = [bg_chain]
        total_meters = len(audio_splitted_channels)
        for i, c in enumerate(audio_splitted_channels):
            chain = "[all_meters_mid_{i}][meter_{ch}]overlay={x}:0[{out}]" \
                    "".format(i=i, ch=c, x=scale_width + 2 + i * meter_width,
                              out='all_meters_mid_{next_i}'.format(next_i=i + 1)
                              if (i + 1) < total_meters else 'all_meters_out')
            overlay_chains.append(chain)
        self._info('Overlaid meters chains: "{}".'.format(';'.join(overlay_chains)))
        graph.extend(overlay_chains)
        return graph, meters_ratio, list(map(lambda x: x.replace('_', ':'), audio_splitted_channels))

    @staticmethod
    def _escape_str(s):
        return s.replace('\\', '\\\\').replace(':', '\\:')

    def _conf_check(self):
        self._info('Checking configuration...')
        # check required parameters
        required_parameters = {
            'BASE_DIR': str, 'FFMPEG_PATH': str, 'FFMPEG_GLOBAL_ARGS': list, 'FFPROBE_PATH': str, 'FFPROBE_ARGS': list,
            'FFPROBE_TIMEOUT': int, 'STATIC_DIR': str, 'LAYOUT_MAP_WIDTH': int, 'FFMPEG_OUT_ARGS': list,
            'FFMPEG_OUT_STR_BUILDER': None, 'EBUR_STATS_FILENAME_TPL': str
        }
        for (p, t) in required_parameters.items():
            try:
                param_value = getattr(self.conf, p)
            except AttributeError:
                raise ConfException('Required parameter is missing: "{}".'.format(p))
            if t is not None and type(param_value) != t:
                raise ConfException('Parameter "{}" must be a {} - {} given.'.format(p, t, type(param_value)))
        # checking dir existence
        self._check_dir_existence({
            'BASE_DIR': self.conf.BASE_DIR,
            'STATIC_DIR': self.conf.STATIC_DIR,
            'LOG_DIR': self.conf.LOG_DIR,
        })
        # checking file existence
        self._check_file_existence({
            'FFMPEG_PATH': self.conf.FFMPEG_PATH,
            'FFPROBE_PATH': self.conf.FFPROBE_PATH,
        })
        # checking file execution
        # tuple format: (ARGS, TIMEOUT, CALLBACK, CB_ERR_TEXT)
        self._check_file_execution({
            'FFMPEG_PATH': (
                [self.conf.FFMPEG_PATH, '-version'], 1, lambda x: x.startswith('ffmpeg version'),
                'FFMPEG_PATH ("{}") is not a ffmpeg executable.'.format(self.conf.FFMPEG_PATH)
            ),
            'FFPROBE_PATH': (
                [self.conf.FFPROBE_PATH, '-version'], 1, lambda x: x.startswith('ffprobe version'),
                'FFPROBE_PATH ("{}") is not a ffprobe executable.'.format(self.conf.FFPROBE_PATH)
            )
        })
        # checking log directory
        tmp_file_path = os.path.join(self.conf.LOG_DIR, 'monitor.tmp')
        try:
            open(tmp_file_path, 'w')
        except OSError as e:
            raise ConfException('Log directory check failed: {}.'.format(str(e)))
        else:
            os.remove(tmp_file_path)

    @staticmethod
    def _check_file_existence(file_dict):
        for (conf_param, path) in file_dict.items():
            if not os.path.isfile(path):
                raise ConfException('{} ("{}") is not an existing file.'.format(conf_param, path))

    @staticmethod
    def _check_dir_existence(dir_dict):
        for (conf_param, path) in dir_dict.items():
            if not os.path.isdir(path):
                raise ConfException('{} ("{}") is not an existing directory.'.format(conf_param, path))

    @staticmethod
    def _check_file_execution(file_dict):
        for (conf_param, check_tuple) in file_dict.items():
            args, timeout, callback, cb_err_text = check_tuple
            try:
                o = subprocess.check_output(args, universal_newlines=True, timeout=timeout, stderr=subprocess.DEVNULL)
                if not callback(o):
                    raise ConfException(cb_err_text)
            except subprocess.TimeoutExpired:
                raise ConfException('{} ("{}") check timeout expired.'.format(conf_param, args[0]))
            except OSError as e:
                raise ConfException('{} ("{}") error: {}'.format(conf_param, args[0], str(e)))

    def _layout_check(self):
        self._info('Checking layout...')
        if self.layout is not None:
            self._info('Already checked!')
            return
        layout_path = self.args.layout
        if not os.path.isabs(layout_path):
            layout_path = os.path.normpath(os.path.join(self.conf.BASE_DIR, layout_path))
        self._info('Loading layout file "{}"...'.format(layout_path))
        if not os.path.isfile(layout_path):
            raise LayoutException(
                'LAYOUT must be a path (relative or absolute) to an existing file, currently '
                '(provided): "{}", (normalized): "{}".'.format(self.args.layout, layout_path)
            )
        try:
            with open(layout_path) as layout_file:
                layout = json.load(layout_file)
        except ValueError as f:
            raise LayoutException('Layout file "{}" is not a valid JSON document: {}'.format(layout_path, str(f)))
        self._info('Checking layout file...')
        if type(layout) != list:
            raise LayoutException('Layout file must contain a list of individual frames parameters - {} given.'.format(
                type(layout)
            ))
        self._info('Checking frames descriptions...')
        required_frame_parameters = {'name', 'x', 'y', 'width', 'height', 'source'}
        frame_parameters_types = {'name': str, 'x': int, 'y': int, 'width': int, 'height': int, 'source': str,
                                  'video_height': int, 'meter_channel_font': str, 'meter_channel_font_size': int}
        map_height = 0
        for (i, f) in enumerate(layout):
            if type(f) != dict:
                raise LayoutException('Frame\'s parameters must be stored in a JSON object '
                                      '- {} given (frame {})'.format(type(f), i))
            if not required_frame_parameters.issubset(f.keys()):
                raise LayoutException('Frame\'s description must include {} (frame {}).'.format(
                    ', '.join(sorted(required_frame_parameters))), i
                )
            for (p, t) in frame_parameters_types.items():
                if type(f.get(p)) != t:
                    raise LayoutException('Frame\'s parameter "{}" must be a {} - {} given (frame {}).'.format(
                        p, t, type(f.get(p)), i
                    ))
            if (f['x'] + f['width']) > self.conf.LAYOUT_MAP_WIDTH:
                raise LayoutException('Frame\'s width exceeds layout map width (frame {})'.format(i))
            map_height = max(map_height, f['y'] + f['height'])
            if f['video_height'] <= 0:
                raise LayoutException('Video height must be a positive integer.')
        layout_map = [['.' for y in range(0, map_height)] for x in range(0, self.conf.LAYOUT_MAP_WIDTH)]
        for (i, f) in enumerate(layout):
            occupied_by_frame = [(x, y) for x in range(f['x'], f['x'] + f['width'])
                                 for y in range(f['y'], f['y'] + f['height'])]
            for (x, y) in occupied_by_frame:
                if layout_map[x][y] != '.':
                    raise LayoutException('Frame intersection detected (({}, {}), frame {}).'.format(x, y, i))
                else:
                    layout_map[x][y] = str(i)
        self._info('Layout map building complete:')
        for y in range(0, map_height):
            self._info(' '.join([layout_map[x][y] for x in range(0, self.conf.LAYOUT_MAP_WIDTH)]))
        self.layout = layout

    def _get_source_info(self, input_path):
        self._info('Trying to fetch source info: "{}"...'.format(input_path))
        try:
            o = subprocess.check_output(
                [self.conf.FFPROBE_PATH] + self.conf.FFPROBE_ARGS + ['-of', 'json', '-show_streams', input_path],
                universal_newlines=True, timeout=self.conf.FFPROBE_TIMEOUT, stderr=subprocess.DEVNULL
            )
        except subprocess.TimeoutExpired:
            raise FrameInputException('Failed to fetch info from "{}" - timeout expired.'.format(input_path))
        return {'streams': json.loads(o)['streams']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '-v', '--verbosity',
        help='verbosity level: 0 - silent, 1 - standard, 2 - verbose',
        type=int,
        default=1
    )
    subparsers = parser.add_subparsers(dest='command', help='command help')
    parser_checkconf = subparsers.add_parser('confcheck', help='check configuration and exit')
    parser_run = subparsers.add_parser('run', help='run')
    parser_run.add_argument(
        '-l', '--layout',
        help='path to layout file',
        required=True,
    )

    app = Application(AppConfiguration, parser.parse_args())
    app.exec()
