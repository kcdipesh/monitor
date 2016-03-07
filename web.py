from bottle import route, run, static_file

from config import AppConfiguration


@route('/static/<filepath:path>')
def server_static(filepath):
    static_file(filepath, AppConfiguration.STATIC_DIR)


if __name__ == '__main__':
    run(host='localhost', port=8080, debug=True)
