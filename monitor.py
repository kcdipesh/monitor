import argparse
import json
import os
import subprocess
import sys

from config import AppConfiguration

VERSION = "dev"


class ConfException(Exception):
    pass


class LayoutException(Exception):
    pass


class FrameInputException(Exception):
    pass


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
        self.layout_source_info = list(map(self._get_source_info, [f['source'] for f in self.layout]))

    def _conf_check(self):
        self._info('Checking configuration...')
        # check required parameters
        required_parameters = {
            'BASE_DIR': str, 'FFMPEG_PATH': str, 'FFMPEG_GLOBAL_ARGS': list, 'FFPROBE_PATH': str, 'FFPROBE_ARGS': list,
            'FFPROBE_TIMEOUT': int, 'AUDIO_METER_CHANNELS': int, 'STATIC_DIR': str, 'LAYOUT_MAP_WIDTH': int
        }
        for (p, t) in required_parameters.items():
            try:
                param_value = getattr(self.conf, p)
            except AttributeError:
                raise ConfException('Required parameter is missing: "{}".'.format(p))
            if type(param_value) != t:
                raise ConfException('Parameter "{}" must be a {} - {} given.'.format(p, t, type(param_value)))
        # checking dir existence
        self._check_dir_existence({
            'BASE_DIR': self.conf.BASE_DIR,
            'STATIC_DIR': self.conf.STATIC_DIR,
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
        frame_parameters_types = {'name': str, 'x': int, 'y': int, 'width': int, 'height': int, 'source': str}
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
        streams = json.loads(o)['streams']
        audio_streams = []
        video_streams = []
        for s in streams:
            if s['codec_type'] == 'video':
                self._info('Found video stream #{}.'.format(s['index']))
                video_streams.append(s)
            elif s['codec_type'] == 'audio':
                self._info('Found audio stream #{}.'.format(s['index']))
                audio_streams.append(s)


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
